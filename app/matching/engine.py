"""Matching engine: exact name, exact alias, then fuzzy (RapidFuzz) with confidence.

Safety rules (they matter more than automation rate):
- A product is auto-selected only when it is the single clear winner.
- Two plausible candidates -> exception "ambiguous"; the engine never picks one.
- Sizes are compared as numbers with units (1 كيلو == 1000 جرام), never as text.
  A size mismatch removes the candidate; it is never "close enough".
- Quantity is never guessed: a missing or unclear quantity is an exception.
Prices and stock are not touched here at all.
"""

import uuid
from dataclasses import dataclass, field
from decimal import Decimal

from rapidfuzz import fuzz

from app.matching.normalize import normalize, strip_definite_article
from app.matching.parser import ParsedLine, Size, alternate_split, parse_line, parse_message

FUZZY_ACCEPT = 90.0      # minimum score to auto-select a fuzzy match
FUZZY_MARGIN = 10.0      # lead over the next different product, otherwise ambiguous
REVIEW_MIN = 70.0        # below this a candidate is not even suggested
CONTAINED_SCORE = 80.0   # order words are a strict subset of the product words
PARTIAL_NUMERIC_SCORE = 95.0  # ... and the missing words are only numbers


@dataclass
class CatalogEntry:
    product_id: uuid.UUID
    sku: str
    name: str
    aliases: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    product_id: uuid.UUID
    sku: str
    name: str
    score: float
    kind: str  # name | alias | fuzzy | contained


@dataclass
class MatchResult:
    raw: str
    status: str  # matched | exception
    quantity: int | None = None
    product_id: uuid.UUID | None = None
    match_type: str | None = None   # exact_name | exact_alias | fuzzy
    confidence: float | None = None
    reason: str | None = None       # not_found | ambiguous | low_confidence | size_mismatch |
    #                                 missing_quantity | unclear_quantity | size_conflict
    candidates: list[Candidate] = field(default_factory=list)


@dataclass
class _Entry:
    product_id: uuid.UUID
    sku: str
    name: str
    key: str
    tokens: frozenset
    size: Size | None
    kind: str  # name | alias


class Matcher:
    def __init__(self, catalog: list[CatalogEntry]):
        self._entries: list[_Entry] = []
        for product in catalog:
            product_line = parse_line(product.name)
            product_size = product_line.size
            self._add(product, product.name, "name", product_size)
            for alias in product.aliases:
                self._add(product, alias, "alias", product_size)

    def _add(self, product: CatalogEntry, text: str, kind: str, fallback_size: Size | None):
        line = parse_line(text)
        # catalog text keeps bare numbers as words (e.g. alias "كوبايات 200");
        # only measures with a unit ("200 مل") become a size
        key = " ".join(line.tokens)
        size = line.size or fallback_size
        self._entries.append(
            _Entry(product.product_id, product.sku, product.name, key,
                   frozenset(key.split()), size, kind)
        )

    # -- scoring ---------------------------------------------------------------
    @staticmethod
    def _score(order_key: str, entry: _Entry) -> tuple[float, str]:
        if not order_key:
            return 0.0, "fuzzy"
        if order_key == entry.key:
            return 100.0, entry.kind
        order_tokens = frozenset(order_key.split())
        # numbers are compared as numbers, never fuzzy-matched as text ("22" is not "2")
        order_nums = {t for t in order_tokens if t.replace(".", "").isdigit()}
        entry_nums = {t for t in entry.tokens if t.replace(".", "").isdigit()}
        if order_nums and order_nums != entry_nums:
            return 0.0, "fuzzy"
        fuzzy = max(fuzz.ratio(order_key, entry.key), fuzz.token_sort_ratio(order_key, entry.key))
        if order_tokens < entry.tokens:
            extra = entry.tokens - order_tokens
            if all(t.replace(".", "").isdigit() for t in extra):
                # only a bare number was left out ("اطباق" for alias "اطباق 22")
                return PARTIAL_NUMERIC_SCORE, "partial"
            if fuzzy < CONTAINED_SCORE:
                return CONTAINED_SCORE, "contained"
        return float(fuzzy), "fuzzy"

    def match_line(self, parsed: ParsedLine) -> MatchResult:
        base = MatchResult(raw=parsed.raw, status="exception", quantity=parsed.quantity)
        if not parsed.key:
            base.reason = "not_found"
            return base
        if "size_conflict" in parsed.flags:
            base.reason = "size_conflict"
            return base

        best: dict[uuid.UUID, tuple[float, str, _Entry]] = {}
        mismatched: dict[uuid.UUID, tuple[float, str, _Entry]] = {}
        for entry in self._entries:
            score, kind = self._score(parsed.key, entry)
            if score < REVIEW_MIN:
                continue
            bucket = best
            if parsed.size is not None and entry.size is not None \
                    and not parsed.size.same_as(entry.size):
                bucket = mismatched
            current = bucket.get(entry.product_id)
            if current is None or score > current[0]:
                bucket[entry.product_id] = (score, kind, entry)

        def to_candidates(pool):
            rows = sorted(pool.values(), key=lambda r: -r[0])[:3]
            return [Candidate(e.product_id, e.sku, e.name, round(s, 1), k) for s, k, e in rows]

        ranked = sorted(best.values(), key=lambda r: -r[0])
        if not ranked:
            if mismatched:
                base.reason = "size_mismatch"
                base.candidates = to_candidates(mismatched)
            else:
                base.reason = "not_found"
            return base

        base.candidates = to_candidates(best)
        top_score, top_kind, top = ranked[0]
        second = ranked[1][0] if len(ranked) > 1 else None

        if top_score == 100.0:
            if second == 100.0:
                base.reason = "ambiguous"
                return base
            decided = ("exact_name" if top_kind == "name" else "exact_alias", 100.0)
        elif top_kind in ("fuzzy", "partial") and top_score >= FUZZY_ACCEPT and (
            second is None or top_score - second >= FUZZY_MARGIN
        ):
            decided = ("fuzzy" if top_kind == "fuzzy" else "partial", top_score)
        else:
            close = second is not None and top_score - second < FUZZY_MARGIN
            base.reason = "ambiguous" if close else "low_confidence"
            return base

        # the product is settled; quantity is a separate question
        base.product_id = top.product_id
        base.match_type = decided[0]
        base.confidence = round(decided[1] / 100.0, 3)
        if parsed.quantity_problem is not None:
            base.reason = "missing_quantity" if parsed.quantity_problem == "missing" \
                else "unclear_quantity"
            return base
        base.status = "matched"
        return base

    def _match_with_two_numbers(self, parsed: ParsedLine) -> MatchResult | None:
        """'٣ كوبايات 200': try each bare number as the quantity, keep the rest as product
        words. Accepted only if exactly one reading settles on a product."""
        winners: list[MatchResult] = []
        for idx in parsed.number_idx:
            value = Decimal(parsed.tokens[idx])
            if value != value.to_integral_value() or value <= 0:
                continue
            rest = [t for i, t in enumerate(parsed.tokens) if i != idx]
            variant = ParsedLine(
                raw=parsed.raw, key=" ".join(rest), size=parsed.size, quantity=int(value),
                tokens=rest,
            )
            result = self.match_line(variant)
            if result.product_id is not None:
                winners.append(result)
        if len(winners) == 1:
            return winners[0]
        return None

    def match_text(self, text: str) -> list[MatchResult]:
        results = self._match_lines(text)
        seen: set[uuid.UUID] = set()
        for r in results:
            if r.status != "matched":
                continue
            if r.product_id in seen:
                # the same product named twice: never merge or overwrite silently
                r.status, r.reason = "exception", "duplicate_product"
            else:
                seen.add(r.product_id)
        return results

    def _match_lines(self, text: str) -> list[MatchResult]:
        results: list[MatchResult] = []
        for parsed in parse_message(text):
            result = self.match_line(parsed)
            if parsed.quantity_problem == "ambiguous" and len(parsed.number_idx) >= 2:
                better = self._match_with_two_numbers(parsed)
                if better is not None:
                    results.append(better)
                    continue
            if result.status != "matched" and result.reason in ("not_found", "low_confidence"):
                alt = self._try_glued_conjunction(parsed)
                if alt is not None:
                    results.extend(alt)
                    continue
            results.append(result)
        return results

    def _try_glued_conjunction(self, parsed: ParsedLine) -> list[MatchResult] | None:
        """'زيت وسكر': accept the split only if both halves match with a product."""
        for left, right in alternate_split(normalize(parsed.raw)) or []:
            a = self.match_line(parse_line(left))
            b = self.match_line(parse_line(right))
            if a.product_id and b.product_id:
                a.raw, b.raw = left, right
                return [a, b]
        return None


def dedupe_key(key: str) -> str:
    return " ".join(strip_definite_article(t) for t in key.split())


__all__ = ["CatalogEntry", "Candidate", "Matcher", "MatchResult"]
