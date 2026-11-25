"""Split an order message into lines and read quantity / size / product text from each.

Sizes ("5 كيلو", "200 مل") and quantities ("٣", "تلاتة") are extracted separately from the
product words, so a size is never mistaken for a quantity and never fuzzy-matched as text.
"""

import re
from dataclasses import dataclass, field
from decimal import Decimal

from app.matching.normalize import normalize, normalize_digits, strip_definite_article

# canonical unit -> (dimension, factor to base unit)
_UNITS = {
    "كيلو": ("mass", Decimal(1000)), "كيلوجرام": ("mass", Decimal(1000)),
    "كجم": ("mass", Decimal(1000)), "كغ": ("mass", Decimal(1000)),
    "kg": ("mass", Decimal(1000)), "كغم": ("mass", Decimal(1000)),
    "جرام": ("mass", Decimal(1)), "جم": ("mass", Decimal(1)), "غرام": ("mass", Decimal(1)),
    "غ": ("mass", Decimal(1)), "g": ("mass", Decimal(1)), "gm": ("mass", Decimal(1)),
    "لتر": ("volume", Decimal(1000)), "ل": ("volume", Decimal(1000)),
    "l": ("volume", Decimal(1000)), "لترات": ("volume", Decimal(1000)),
    "مل": ("volume", Decimal(1)), "ml": ("volume", Decimal(1)),
    "سم": ("length", Decimal(1)), "cm": ("length", Decimal(1)),
    "متر": ("length", Decimal(100)), "m": ("length", Decimal(100)),
}
_FRACTIONS = {"نص": Decimal("0.5"), "نصف": Decimal("0.5"), "ربع": Decimal("0.25"),
              "تلت": Decimal(1) / Decimal(3)}
_BARE_ONE_UNITS = {"كيلو", "لتر"}  # "سكر كيلو" means 1 kg

_NUMBER_WORDS = {
    "واحد": 1, "واحده": 1, "اتنين": 2, "اثنين": 2, "اثنان": 2, "تنين": 2, "تلاته": 3,
    "ثلاثه": 3, "تلات": 3, "اربعه": 4, "اربع": 4, "خمسه": 5, "خمس": 5, "سته": 6, "ست": 6,
    "سبعه": 7, "سبع": 7, "تمانيه": 8, "ثمانيه": 8, "تمن": 8, "تسعه": 9, "تسع": 9,
    "عشره": 10, "عشر": 10,
    # dual forms: "كرتونتين" is already "2 cartons" (unit word + quantity in one)
    "كرتونتين": 2, "كارتونتين": 2, "شكارتين": 2, "شيكارتين": 2, "علبتين": 2, "قطعتين": 2,
    "كيسين": 2, "زجاجتين": 2, "قزازتين": 2, "باكوتين": 2, "دستتين": 2,
}
_FILLER = {
    "عايز", "عاوز", "عايزه", "عاوزه", "محتاج", "محتاجه", "ابعتلي", "ابعت", "هات", "هاتلي",
    "هاتي", "لو", "سمحت", "فضلك", "من", "طلب", "طلبيه", "الطلب", "عندي", "ممكن", "يا",
    "باشا", "استاذ", "اريد", "ارجو", "طلبيتي", "كمان", "برضه", "وكمان", "و", "في", "عدد",
    "شكاير", "شيكاير", "شكاره", "شيكاره",
    "علبه", "علب", "كيس", "اكياس", "شوال", "كرتونه", "كراتين", "كارتونه", "قطعه", "قطع",
    "حبه", "حبات", "باكو", "زجاجه", "قزازه", "ازازه", "دسته", "الي", "اللي", "تبعت", "ببعت",
}
_SPLIT = re.compile(r"[\n\r\u060c,;\u061b+&\u2022|]+")
_LIST_MARKER = re.compile(r"^\s*\d+\s*[\-.)]\s+")
_TIMES = re.compile(r"(?:^|\s)(?:x|×|\*)\s*(\d+)(?=\s|$)|(?<=\d)\s*(?:x|×)(?=\s|$)")


@dataclass
class Size:
    dimension: str
    amount: Decimal  # in the dimension's base unit (g, ml, cm)

    def same_as(self, other: "Size") -> bool:
        return self.dimension == other.dimension and self.amount == other.amount

    def label(self) -> str:
        base = {"mass": "g", "volume": "ml"}.get(self.dimension, "cm")
        return f"{self.amount.normalize():f} {base}"


@dataclass
class ParsedLine:
    raw: str
    key: str                      # product words only (normalized, article-stripped)
    size: Size | None = None
    quantity: int | None = None
    quantity_problem: str | None = None   # "missing" | "ambiguous" | "not_integer"
    flags: list[str] = field(default_factory=list)
    # all remaining tokens (words and bare numbers, in order) and where the numbers are;
    # used when a line has two bare numbers ("٣ كوبايات 200") and the catalog decides
    tokens: list[str] = field(default_factory=list)
    number_idx: list[int] = field(default_factory=list)


def _is_number(tok: str) -> bool:
    return re.fullmatch(r"\d+(?:\.\d+)?", tok) is not None


def _extract_size(tokens: list[str]) -> tuple[Size | None, list[str], bool]:
    """Remove the first 'number unit' / 'fraction unit' / bare 'كيلو' expression.
    Returns (size, remaining tokens, size_conflict)."""
    found: list[Size] = []
    out: list[str] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        nxt = tokens[i + 1] if i + 1 < len(tokens) else None
        if _is_number(tok) and nxt in _UNITS:
            dim, factor = _UNITS[nxt]
            found.append(Size(dim, Decimal(tok) * factor))
            i += 2
            continue
        if tok in _FRACTIONS and nxt in _UNITS:
            dim, factor = _UNITS[nxt]
            found.append(Size(dim, (_FRACTIONS[tok] * factor).quantize(Decimal("0.01"))))
            i += 2
            continue
        out.append(tok)
        i += 1
    if not found:
        # bare "كيلو"/"لتر" with no number: one unit
        rest: list[str] = []
        for tok in out:
            if tok in _BARE_ONE_UNITS and not found:
                dim, factor = _UNITS[tok]
                found.append(Size(dim, factor))
            else:
                rest.append(tok)
        out = rest
    if not found:
        return None, out, False
    conflict = any(not found[0].same_as(s) for s in found[1:])
    return found[0], out, conflict


def parse_line(raw: str) -> ParsedLine:
    text = _TIMES.sub(lambda m: f" {m.group(1)} " if m.group(1) else " ", normalize_digits(raw))
    text = _LIST_MARKER.sub("", text)
    tokens = normalize(text).split()
    size, tokens, conflict = _extract_size(tokens)
    flags = ["size_conflict"] if conflict else []

    tokens = [t for t in tokens if t not in _FILLER]
    numbers = [t for t in tokens if _is_number(t)]
    words = [t for t in tokens if not _is_number(t)]
    quantity = None
    problem = None
    if len(numbers) == 1:
        value = Decimal(numbers[0])
        if value != value.to_integral_value() or value <= 0:
            problem = "not_integer"
        else:
            quantity = int(value)
    elif len(numbers) > 1:
        problem = "ambiguous"
    else:
        hits = [t for t in words if t in _NUMBER_WORDS]
        if len(hits) == 1:
            quantity = _NUMBER_WORDS[hits[0]]
            words = [t for t in words if t != hits[0]]
        elif len(hits) > 1:
            problem = "ambiguous"
        else:
            problem = "missing"
    words = [strip_definite_article(t) for t in words]
    all_tokens = [strip_definite_article(t) for t in tokens]
    return ParsedLine(raw=raw.strip(), key=" ".join(words), size=size, quantity=quantity,
                      quantity_problem=problem, flags=flags, tokens=all_tokens,
                      number_idx=[i for i, t in enumerate(all_tokens) if _is_number(t)])


def split_message(text: str) -> list[str]:
    """Split on newlines, commas, '+', and the standalone word 'و'."""
    parts: list[str] = []
    for chunk in _SPLIT.split(normalize_digits(text)):
        if not chunk.strip():
            continue
        sub = re.split(r"(?:(?<=\s)|^)[\u0648](?=\s)", chunk)  # standalone و
        parts.extend(p.strip() for p in sub if p.strip())
    return parts


def alternate_split(raw: str) -> list[tuple[str, str]] | None:
    """For 'زيت وسكر' (و glued to the next word): all places where a token starting
    with و could start a new item. Returns the list of two-part splits or None."""
    toks = raw.split()
    splits = []
    for i, tok in enumerate(toks):
        if i > 0 and tok.startswith("و") and len(tok) > 3:
            left = " ".join(toks[:i])
            right = " ".join([tok[1:], *toks[i + 1:]])
            splits.append((left, right))
    return splits or None


def parse_message(text: str) -> list[ParsedLine]:
    return [parse_line(p) for p in split_message(text)]
