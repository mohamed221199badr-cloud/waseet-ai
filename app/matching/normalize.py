"""Arabic-aware text normalization used by both the catalog and the order text."""

import re
import unicodedata

_DIACRITICS = re.compile("[\u064b-\u0652\u0670\u0640]")
_ARABIC_DIGITS = str.maketrans(
    "٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹٫٬", "01234567890123456789.,"
)
_LETTER_MAP = str.maketrans(
    {"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"}
)
_PUNCT = re.compile(r"[^\w\s.]", re.UNICODE)


def normalize_digits(text: str) -> str:
    return unicodedata.normalize("NFKC", text).translate(_ARABIC_DIGITS)


def normalize(text: str) -> str:
    """Lowercase, strip diacritics/tatweel, unify alef/ya/ta-marbuta, ASCII digits,
    drop punctuation, split digits from letters, collapse spaces."""
    text = normalize_digits(text).lower()
    text = _DIACRITICS.sub("", text).translate(_LETTER_MAP)
    text = _PUNCT.sub(" ", text).replace("_", " ")
    text = re.sub(r"(?<=\d)(?=[^\d\s.])|(?<=[^\d\s.])(?=\d)", " ", text)
    text = re.sub(r"(?<!\d)\.|\.(?!\d)", " ", text)
    return " ".join(text.split())


def strip_definite_article(token: str) -> str:
    """'الزيت' -> 'زيت' (only for longer tokens, so 'الف' style words survive)."""
    if token.startswith("ال") and len(token) >= 5:
        return token[2:]
    return token
