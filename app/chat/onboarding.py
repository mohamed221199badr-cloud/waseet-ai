"""Mandatory customer onboarding after /start, before the main menu (19758722 F2).

Collects phone (native Telegram contact share via Reply Keyboard), delivery vs
pickup (inline buttons), and an address only for delivery. CRM persistence is
incremental on the customers row (phone/address/delivery_method columns), so an
interrupted onboarding resumes at the first missing field and returning customers
are never re-asked. Step flags ride the SAME menu ChatSession draft["ui"] - no
parallel session mechanism. All deterministic, no LLM.

Gate: service calls current_step before any menu flow; while the profile is
incomplete only the contact message, the dv: callbacks, address text and /stop
get through - everything else is answered with the current step (no bypass).
"""

import re

from sqlalchemy.orm import Session

from app.chat import callbacks, menu_buttons
from app.matching.normalize import normalize_digits
from app.models_orders import Customer, TelegramLink

MSG_PHONE = ("أهلاً بيك ❤️\n"
             "علشان نقدر نستقبل طلبك، محتاجين رقم تليفونك للتواصل معاك.")
BTN_SHARE_CONTACT = "📱 مشاركة رقم التليفون"
MSG_CONTACT_NOT_YOURS = "لازم تشارك رقمك أنت من زرار المشاركة، مش رقم حد تاني."
MSG_PHONE_BAD = "الرقم ده مش صح. جرب تشاركه تاني من الزرار تحت."
MSG_DELIVERY_ASK = "تمام ✅ تحب طلبك يوصلك لحد عندك ولا تستلمه من المحل؟"
MSG_ADDRESS_ASK = "اكتب عنوانك بالتفصيل (المنطقة، الشارع، رقم العمارة والشقة):"
MSG_ADDRESS_BAD = "العنوان قصير أوي. اكتبه بالتفصيل لو سمحت."

_CONTACT_KEYBOARD = [[{"text": BTN_SHARE_CONTACT, "request_contact": True}]]


def is_complete(customer: Customer) -> bool:
    """CRM truth, not session state: whatever is already stored is never re-asked."""
    if not customer.phone or not customer.delivery_method:
        return False
    return customer.delivery_method != "delivery" or bool(customer.address)


def _ensure_ui(db: Session, link: TelegramLink):
    _session, ui, _lines = menu_buttons._ensure_session(db, link)
    return ui


def current_step(db: Session, link: TelegramLink) -> dict | None:
    """None when the profile is complete. Otherwise the step to show:
    {"text", "reply_keyboard", "rows", "remove_keyboard"}."""
    customer = db.get(Customer, link.customer_id)
    if customer is None or is_complete(customer):
        return None
    if not customer.phone:
        ui = _ensure_ui(db, link)
        ui["awaiting_contact"] = True
        ui["waiting_for_address"] = False
        menu_buttons._save(db, _session_of(db, link), ui)
        return {"text": MSG_PHONE, "reply_keyboard": _CONTACT_KEYBOARD,
                "rows": None, "remove_keyboard": False}
    if not customer.delivery_method:
        return {"text": MSG_DELIVERY_ASK, "reply_keyboard": None,
                "rows": [[("🚚 توصيل", callbacks.build("dv", "delivery")),
                          ("🏪 استلام من المحل", callbacks.build("dv", "pickup"))]],
                "remove_keyboard": True}
    # delivery without an address yet
    ui = _ensure_ui(db, link)
    ui["awaiting_contact"] = False
    ui["waiting_for_address"] = True
    menu_buttons._save(db, _session_of(db, link), ui)
    return {"text": MSG_ADDRESS_ASK, "reply_keyboard": None, "rows": None,
            "remove_keyboard": True}


def _session_of(db: Session, link: TelegramLink):
    session, _ui, _lines = menu_buttons._ensure_session(db, link)
    return session


def _done_or_next(db: Session, link: TelegramLink) -> dict:
    step = current_step(db, link)
    if step is not None:
        return step
    ui = _ensure_ui(db, link)
    ui["awaiting_contact"] = False
    ui["waiting_for_address"] = False
    menu_buttons._save(db, _session_of(db, link), ui)
    text, rows = menu_buttons.welcome(db, link)
    return {"text": text, "reply_keyboard": None, "rows": rows, "remove_keyboard": True}


def normalize_phone(raw: str) -> str | None:
    """Digits (Arabic-Indic safe), optional leading +; 8-15 digits or None."""
    t = normalize_digits(raw).strip()
    plus = t.startswith("+")
    digits = re.sub(r"\D", "", t)
    if not 8 <= len(digits) <= 15:
        return None
    return ("+" if plus else "") + digits


def handle_contact(db: Session, link: TelegramLink, contact: dict,
                   sender_id: int | None) -> dict:
    """A shared-contact message while the phone step is open."""
    ui = _ensure_ui(db, link)
    if not ui.get("awaiting_contact"):
        return current_step(db, link) or _done_or_next(db, link)
    # ownership: Telegram stamps the sender's user_id on a natively shared contact
    if contact.get("user_id") is None or contact.get("user_id") != sender_id:
        return {"text": MSG_CONTACT_NOT_YOURS, "reply_keyboard": _CONTACT_KEYBOARD,
                "rows": None, "remove_keyboard": False}
    phone = normalize_phone(str(contact.get("phone_number") or ""))
    if phone is None:
        return {"text": MSG_PHONE_BAD, "reply_keyboard": _CONTACT_KEYBOARD,
                "rows": None, "remove_keyboard": False}
    customer = db.get(Customer, link.customer_id)
    if customer is not None and not customer.phone:  # never overwrite valid CRM data
        customer.phone = phone
    ui["awaiting_contact"] = False
    menu_buttons._save(db, _session_of(db, link), ui)
    return _done_or_next(db, link)


_DELIVERY_WORDS = ("توصيل", "دليفري", "لحد عندي", "لحد البيت", "المنزل", "هوصل")
_PICKUP_WORDS = ("استلام", "من المحل", "بيك اب", "pickup", "هستلم")


def delivery_answer(text: str) -> str | None:
    """Typed fallback for the delivery/pickup step (hotfix 19793273): customers who
    type the answer instead of tapping. Normalized word match, delivery first."""
    from app.matching.normalize import normalize
    t = normalize(text)
    if not t:
        return None
    if any(normalize(w) in t for w in _DELIVERY_WORDS):
        return "delivery"
    if any(normalize(w) in t for w in _PICKUP_WORDS):
        return "pickup"
    return None


def handle_delivery_choice(db: Session, link: TelegramLink, method: str) -> dict:
    customer = db.get(Customer, link.customer_id)
    if customer is None or customer.phone is None:
        return current_step(db, link) or _done_or_next(db, link)
    if not customer.delivery_method:
        customer.delivery_method = method
    ui = _ensure_ui(db, link)
    if method == "pickup" or customer.address:
        ui["waiting_for_address"] = False
        menu_buttons._save(db, _session_of(db, link), ui)
    return _done_or_next(db, link)


def wants_address(db: Session, link: TelegramLink) -> bool:
    ui = _ensure_ui(db, link)
    return bool(ui.get("waiting_for_address"))


def wants_contact(db: Session, link: TelegramLink) -> bool:
    session = menu_buttons._session(db, link)
    ui = menu_buttons._ui(session)
    return bool(ui and ui.get("awaiting_contact"))


def handle_address_text(db: Session, link: TelegramLink, text: str) -> dict:
    """A text message while the address step is open."""
    ui = _ensure_ui(db, link)
    if not ui.get("waiting_for_address"):
        return current_step(db, link) or _done_or_next(db, link)
    address = text.strip()
    if len(address) < 5:
        return {"text": MSG_ADDRESS_BAD, "reply_keyboard": None, "rows": None,
                "remove_keyboard": False}
    customer = db.get(Customer, link.customer_id)
    if customer is not None and not customer.address:
        customer.address = address[:500]
    ui["waiting_for_address"] = False
    menu_buttons._save(db, _session_of(db, link), ui)
    return _done_or_next(db, link)
