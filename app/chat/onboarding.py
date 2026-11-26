"""Shared contact/phone helpers for the chat flows.

Used by checkout (app/chat/checkout.py): phone normalization (Arabic-Indic
digit safe) and the typed delivery/pickup fallback (hotfix 19793273) for
customers who type the answer instead of tapping a button.
"""

import re

from app.matching.normalize import normalize_digits

MSG_CONTACT_NOT_YOURS = "لازم تشارك رقمك أنت من زرار المشاركة، مش رقم حد تاني."
MSG_PHONE_BAD = "الرقم ده مش صح. جرب تشاركه تاني من الزرار تحت."


def normalize_phone(raw: str) -> str | None:
    """Digits (Arabic-Indic safe), optional leading +; 8-15 digits or None."""
    t = normalize_digits(raw).strip()
    plus = t.startswith("+")
    digits = re.sub(r"\D", "", t)
    if not 8 <= len(digits) <= 15:
        return None
    return ("+" if plus else "") + digits


_DELIVERY_WORDS = ("توصيل", "دليفري", "لحد عندي", "لحد البيت", "المنزل", "هوصل")
_PICKUP_WORDS = ("استلام", "من المحل", "بيك اب", "pickup", "هستلم")


def delivery_answer(text: str) -> str | None:
    """Typed fallback for the delivery/pickup question (hotfix 19793273):
    customers who type the answer instead of tapping. Normalized word match,
    delivery first."""
    from app.matching.normalize import normalize
    t = normalize(text)
    if not t:
        return None
    if any(normalize(w) in t for w in _DELIVERY_WORDS):
        return "delivery"
    if any(normalize(w) in t for w in _PICKUP_WORDS):
        return "pickup"
    return None
