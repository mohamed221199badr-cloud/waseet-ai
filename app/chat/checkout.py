"""Checkout after the cart confirm (WhatsApp spec, 2026-11-26): phone -> delivery
type -> address -> final summary, then the order goes to the cashier.

No onboarding at /start: profile data is collected here, at checkout, after the
customer taps [✅ تأكيد الطلب] (cok) in the cart — or types «تمام» on an open
confirmation, which starts the same checkout. Steps ride the SAME menu
ChatSession draft["ui"] (checkout_step + temp_delivery_type/temp_address), so
there is no parallel session mechanism; the cart lines stay in the draft until
final_confirm clears the session. All deterministic, no LLM.

Steps (ui["checkout_step"]): awaiting_phone -> awaiting_delivery_type ->
awaiting_address (delivery only) -> awaiting_final_confirm. The phone step is
skipped when the CRM already has a phone; the address step is skipped for
pickup, and a saved address is offered for reuse first. Cancel (❌ or a typed
cancel word) clears only the checkout state and keeps the cart.

final_confirm creates the order through menu_agent._create_order (price freeze,
OrderExceptions, audit) with status "pending" — main's cashier-visible state.
('awaiting_confirmation' belongs to the unmerged orders-flow branch: ck_orders_status
and VARCHAR(20) reject it on main. Switch the status argument when that merges.)
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.chat import agent as legacy
from app.chat import callbacks, cashier_actions, menu_agent, menu_buttons, onboarding
from app.config import settings
from app.matching.normalize import normalize
from app.models_orders import Customer, OrderException, TelegramLink

MSG_PHONE = "محتاجين رقمك عشان نأكد الطلب."
BTN_SHARE_CONTACT = "📱 مشاركة رقم الموبايل"
MSG_DELIVERY_TYPE = "الطلب دليفري ولا استلام؟"
MSG_ADDRESS_ASK = "اكتب العنوان بالتفصيل (المنطقة، الشارع، علامة مميزة)."
MSG_ADDRESS_BAD = "العنوان قصير أوي. اكتبه بالتفصيل لو سمحت."
MSG_ADDRESS_REUSE = "هتستلم على العنوان السابق؟\n{address}"
MSG_DONE = "تمام ✅ طلبك اتبعت للكاشير وجاري التنفيذ.\nرقم الطلب: {oid}"
MSG_DONE_CONFIRMED = "طلبك اتأكد ✅ وجاري تجهيزه.\nرقم الطلب: {oid}"
# typed words that confirm the order, honoured ONLY at the final-confirm step
FINISH = {normalize(w) for w in ("تمام", "تم", "إنهاء", "انهاء", "إنهاء الطلب",
                                       "انهاء الطلب", "خلصت", "تمام خلصت", "خلاص",
                                       "تأكيد", "تاكيد", "اكد", "أكد", "ok", "اوك")}

BTN_BACK_TEXT = "🔙 رجوع"
# a reply keyboard cannot carry inline buttons: the back button is a keyboard button whose
# text handle_text maps back to the cart
_CONTACT_KEYBOARD = [[{"text": BTN_SHARE_CONTACT, "request_contact": True}],
                     [{"text": BTN_BACK_TEXT}]]
_BACK_CART_ROWS = [[("🔙 رجوع", callbacks.build("cart"))]]
_METHOD_LABEL = {"delivery": "دليفري 🛵", "pickup": "استلام من المحل 🏪"}

CALLBACK_ACTIONS = ("cok", "ckdel", "ckaddr", "ckok", "ckno")

_STEP_KEYS = ("checkout_step", "temp_delivery_type", "temp_address")


def _spec(text, rows=None, reply_keyboard=None, remove_keyboard=False,
          edit_id=None):
    return {"text": text, "rows": rows, "reply_keyboard": reply_keyboard,
            "remove_keyboard": remove_keyboard, "edit_message_id": edit_id}


def _ui_of(db: Session, link: TelegramLink):
    session, ui, lines = menu_buttons._ensure_session(db, link)
    return session, ui, lines


def _clear_checkout(ui: dict) -> None:
    for key in _STEP_KEYS:
        ui.pop(key, None)


def active(db: Session, link: TelegramLink) -> bool:
    """True while any checkout step is open."""
    session = menu_buttons._session(db, link)
    ui = menu_buttons._ui(session)
    return bool(ui and ui.get("checkout_step"))


def awaiting_contact(db: Session, link: TelegramLink) -> bool:
    session = menu_buttons._session(db, link)
    ui = menu_buttons._ui(session)
    return bool(ui and ui.get("checkout_step") == "awaiting_phone")


def _delivery_type_spec(ui: dict, edit_id=None) -> dict:
    ui["checkout_step"] = "awaiting_delivery_type"
    return _spec(MSG_DELIVERY_TYPE,
                 rows=[[("🛵 دليفري", callbacks.build("ckdel", "delivery")),
                        ("🏪 استلام", callbacks.build("ckdel", "pickup"))],
                       [("🔙 رجوع", callbacks.build("cart"))]],
                 remove_keyboard=True, edit_id=edit_id)


def _summary_spec(db: Session, link: TelegramLink, ui: dict, lines: list[dict],
                  edit_id=None) -> dict:
    """Items + total + delivery type + address, with the final confirm/cancel."""
    ui["checkout_step"] = "awaiting_final_confirm"
    cat = menu_buttons.load_catalog(db, link.business_id)
    cart = menu_buttons._cart_text(db, link, cat, lines)
    body = cart.split("\n", 1)[1] if cart.startswith("السلة:\n") else cart
    method = ui.get("temp_delivery_type") or "pickup"
    parts = ["مراجعة الطلب النهائي:", body, f"الطلب: {_METHOD_LABEL[method]}"]
    if method == "delivery" and ui.get("temp_address"):
        parts.append(f"العنوان: {ui['temp_address']}")
    return _spec("\n".join(parts),
                 rows=[[("✅ تأكيد وإرسال للكاشير", callbacks.build("ckok"))],
                       [("❌ إلغاء", callbacks.build("ckno"))],
                       [("🔙 رجوع", callbacks.build("cart"))]],
                 remove_keyboard=True, edit_id=edit_id)


def _after_method(db: Session, link: TelegramLink, ui: dict, lines: list[dict],
                  method: str, edit_id=None) -> dict:
    """Delivery type chosen: pickup skips the address; delivery asks for one."""
    ui["temp_delivery_type"] = method
    if method == "pickup":
        ui.pop("temp_address", None)
        return _summary_spec(db, link, ui, lines, edit_id)
    customer = db.get(Customer, link.customer_id)
    saved = (customer.address or "").strip() if customer is not None else ""
    if saved:
        ui["checkout_step"] = "awaiting_address_reuse"
        return _spec(MSG_ADDRESS_REUSE.format(address=saved[:300]),
                     rows=[[("أيوه", callbacks.build("ckaddr", "keep")),
                            ("لأ، غير العنوان", callbacks.build("ckaddr", "change"))],
                            [("🔙 رجوع", callbacks.build("cart"))]],
                     remove_keyboard=True, edit_id=edit_id)
    ui["checkout_step"] = "awaiting_address"
    return _spec(MSG_ADDRESS_ASK, rows=_BACK_CART_ROWS, remove_keyboard=True, edit_id=edit_id)


def begin(db: Session, link: TelegramLink, edit_id=None) -> dict:
    """Start (or restart) checkout: the first step the customer still needs."""
    session, ui, lines = _ui_of(db, link)
    _clear_checkout(ui)
    if edit_id is not None:
        ui["msg_id"] = edit_id
    if not menu_buttons._ok_lines(lines):
        _clear_checkout(ui)
        ui.update(screen="cats")
        menu_buttons._save(db, session, ui)
        cat_rows = menu_buttons._cats_rows(db, link, lines)
        return _spec(menu_buttons.MSG_CART_EMPTY, rows=cat_rows, edit_id=edit_id)
    customer = db.get(Customer, link.customer_id)
    if customer is None or not customer.phone:
        ui["checkout_step"] = "awaiting_phone"
        menu_buttons._save(db, session, ui)
        # a ReplyKeyboardMarkup can't ride an edit: fresh message
        return _spec(MSG_PHONE, reply_keyboard=_CONTACT_KEYBOARD)
    spec = _delivery_type_spec(ui, edit_id)
    menu_buttons._save(db, session, ui)
    return spec


def current_spec(db: Session, link: TelegramLink) -> dict:
    """Re-render the open step (typed-message re-shows and post-begin renders)."""
    session, ui, lines = _ui_of(db, link)
    step = ui.get("checkout_step")
    if step == "awaiting_phone":
        return _spec(MSG_PHONE, reply_keyboard=_CONTACT_KEYBOARD)
    if step == "awaiting_delivery_type":
        return _delivery_type_spec(ui, ui.get("msg_id"))
    if step == "awaiting_address_reuse":
        customer = db.get(Customer, link.customer_id)
        saved = (customer.address or "").strip() if customer is not None else ""
        return _spec(MSG_ADDRESS_REUSE.format(address=saved[:300]),
                     rows=[[("أيوه", callbacks.build("ckaddr", "keep")),
                            ("لأ، غير العنوان", callbacks.build("ckaddr", "change"))],
                            [("🔙 رجوع", callbacks.build("cart"))]],
                     remove_keyboard=True, edit_id=ui.get("msg_id"))
    if step == "awaiting_address":
        return _spec(MSG_ADDRESS_ASK, rows=_BACK_CART_ROWS, remove_keyboard=True,
                     edit_id=ui.get("msg_id"))
    if step == "awaiting_final_confirm":
        return _summary_spec(db, link, ui, lines, ui.get("msg_id"))
    # nothing open: fall back to the cart
    return _cart_spec(db, link, ui, lines)


def _cart_spec(db: Session, link: TelegramLink, ui: dict, lines: list[dict],
               edit_id=None) -> dict:
    _clear_checkout(ui)
    ui.update(screen="cart")
    session = menu_buttons._session(db, link)
    menu_buttons._save(db, session, ui)
    cat = menu_buttons.load_catalog(db, link.business_id)
    return _spec(menu_buttons._cart_text(db, link, cat, lines),
                 rows=menu_buttons._cart_rows(), remove_keyboard=True,
                 edit_id=edit_id if edit_id is not None else ui.get("msg_id"))


def handle_contact(db: Session, link: TelegramLink, contact: dict,
                   sender_id: int | None) -> dict:
    """A shared-contact message while awaiting_phone is open."""
    session, ui, lines = _ui_of(db, link)
    if ui.get("checkout_step") != "awaiting_phone":
        return current_spec(db, link)
    # ownership: Telegram stamps the sender's user_id on a natively shared contact
    if contact.get("user_id") is None or contact.get("user_id") != sender_id:
        return _spec(onboarding.MSG_CONTACT_NOT_YOURS,
                     reply_keyboard=_CONTACT_KEYBOARD)
    phone = onboarding.normalize_phone(str(contact.get("phone_number") or ""))
    if phone is None:
        return _spec(onboarding.MSG_PHONE_BAD, reply_keyboard=_CONTACT_KEYBOARD)
    customer = db.get(Customer, link.customer_id)
    if customer is not None and not customer.phone:  # never overwrite valid CRM data
        customer.phone = phone
    spec = _delivery_type_spec(ui)
    menu_buttons._save(db, session, ui)
    return spec


def handle_callback(db: Session, link: TelegramLink, cb: callbacks.Callback,
                    msg_id: int | None) -> dict:
    """cok/ck* taps. msg_id is the tapped message (edit target)."""
    session, ui, lines = _ui_of(db, link)
    if msg_id is not None:
        ui["msg_id"] = msg_id

    if cb.action == "cok":
        return begin(db, link, edit_id=msg_id)

    if not ui.get("checkout_step"):
        return _cart_spec(db, link, ui, lines, edit_id=msg_id)

    if cb.action == "ckno":
        # cancel checkout only; the cart stays
        return _cart_spec(db, link, ui, lines, edit_id=msg_id)

    if cb.action == "ckdel":
        spec = _after_method(db, link, ui, lines, cb.args[0], edit_id=msg_id)
        menu_buttons._save(db, session, ui)
        return spec

    if cb.action == "ckaddr":
        if ui.get("checkout_step") != "awaiting_address_reuse":
            return current_spec(db, link)
        if cb.args[0] == "keep":
            customer = db.get(Customer, link.customer_id)
            ui["temp_address"] = ((customer.address or "").strip()[:500]
                                  if customer is not None else "")
            spec = _summary_spec(db, link, ui, lines, edit_id=msg_id)
        else:
            ui.pop("temp_address", None)
            ui["checkout_step"] = "awaiting_address"
            spec = _spec(MSG_ADDRESS_ASK, rows=_BACK_CART_ROWS, remove_keyboard=True,
                         edit_id=msg_id)
        menu_buttons._save(db, session, ui)
        return spec

    # ckok: final confirm
    if ui.get("checkout_step") != "awaiting_final_confirm":
        return current_spec(db, link)
    return _finalize(db, link, session, ui, lines, edit_id=msg_id)


MSG_CASHIER = ("🔔 طلب جديد رقم {oid}\n"
               "العميل: {name}{phone}\n"
               "{body}\n"
               "الطلب: {method}{addr}")


def _queue_cashier_notice(db: Session, link: TelegramLink, customer, order,
                          lines: list[dict], method: str, address: str,
                          buttons: bool = True) -> None:
    """Immediate message to every owner chat (the shop's cashier/owner links) when an
    order is confirmed. Sent by the worker after the customer's reply."""
    cat = menu_buttons.load_catalog(db, link.business_id)
    cart = menu_buttons._cart_text(db, link, cat, lines)
    body = cart.split("\n", 1)[1] if cart.startswith("السلة:\n") else cart
    text = MSG_CASHIER.format(
        oid=str(order.id)[:8], name=customer.name,
        phone=f" - {customer.phone}" if customer.phone else "", body=body,
        method=_METHOD_LABEL[method],
        addr=f"\nالعنوان: {address}" if method == "delivery" and address else "")
    owners = db.scalars(select(TelegramLink).where(
        TelegramLink.business_id == link.business_id,
        TelegramLink.is_owner.is_(True))).all()
    chat_ids = list(dict.fromkeys(
        [o.chat_id for o in owners] + settings.cashier_telegram_chats))
    if not buttons:
        text = text.replace("🔔 طلب جديد رقم", "🔔 طلب جديد (متأكد تلقائياً ✅) رقم", 1)
    if buttons:
        rows = cashier_actions.notice_rows(order.id)
        db.info.setdefault("order_notify", []).extend((cid, text, rows) for cid in chat_ids)
    else:
        db.info.setdefault("order_notify", []).extend((cid, text) for cid in chat_ids)


def _finalize(db: Session, link: TelegramLink, session, ui: dict,
              lines: list[dict], edit_id=None) -> dict:
    customer = db.get(Customer, link.customer_id)
    if customer is None:
        return _cart_spec(db, link, ui, lines, edit_id=edit_id)
    method = ui.get("temp_delivery_type") or "pickup"
    address = (ui.get("temp_address") or "").strip()[:500]
    customer.delivery_method = method  # this order's choice, kept for next time
    if method == "delivery" and address:
        customer.address = address
    notes = f"{_METHOD_LABEL[method]}" + (f" - {address}" if method == "delivery"
                                          and address else "")
    cat = menu_buttons.load_catalog(db, link.business_id)
    order = menu_agent._create_order(db, link, cat, lines, status="pending",
                                     notes=notes[:500])
    # auto-confirm (default): the order is confirmed the moment the customer sends it,
    # unless a line could not be priced (an open exception): that one stays pending for
    # the cashier's ✅/❌ and reminders
    open_exc = db.scalar(select(func.count()).select_from(OrderException).where(
        OrderException.order_id == order.id)) or 0
    auto = settings.order_auto_confirm and open_exc == 0
    if auto:
        order.status = "confirmed"
        order.decided_at = datetime.now(timezone.utc)
    write_audit(db, business_id=link.business_id, entity="order",
                entity_id=str(order.id), action="checkout",
                payload={"status": order.status, "delivery_method": method,
                         "has_address": bool(address), "auto_confirmed": auto})
    _queue_cashier_notice(db, link, customer, order, lines, method, address,
                          buttons=not auto)
    if auto:
        cashier_actions.forward(db, order, customer,
                                cashier_actions._lines(db, order), "تلقائي")
    legacy._clear(db, session)  # spec: clear the session draft on final confirm
    return _spec((MSG_DONE_CONFIRMED if auto else MSG_DONE).format(oid=str(order.id)[:8]),
                 rows=[[("✅ تمام، خلصت", callbacks.build("done"))],
                       [("🛒 طلب جديد", callbacks.build("cats"))]],
                 remove_keyboard=True, edit_id=edit_id)


def handle_text(db: Session, link: TelegramLink, text: str) -> dict:
    """A text message while any checkout step is open."""
    session, ui, lines = _ui_of(db, link)
    step = ui.get("checkout_step")
    if not step:
        return _cart_spec(db, link, ui, lines)

    if text.strip() == BTN_BACK_TEXT:  # the reply-keyboard back button: back to the cart
        return _cart_spec(db, link, ui, lines)

    if legacy._is(text, legacy.CANCEL):
        # cancel checkout only; the cart stays
        return _cart_spec(db, link, ui, lines)

    if step == "awaiting_delivery_type":
        method = onboarding.delivery_answer(text)
        if method is not None:
            spec = _after_method(db, link, ui, lines, method,
                                 edit_id=ui.get("msg_id"))
            menu_buttons._save(db, session, ui)
            return spec
        return current_spec(db, link)

    if step == "awaiting_address":
        address = text.strip()
        if len(address) < 5:
            return _spec(MSG_ADDRESS_BAD, rows=_BACK_CART_ROWS, edit_id=ui.get("msg_id"))
        ui["temp_address"] = address[:500]
        spec = _summary_spec(db, link, ui, lines, edit_id=ui.get("msg_id"))
        menu_buttons._save(db, session, ui)
        return spec

    if step == "awaiting_final_confirm" and legacy._is(text, FINISH):
        # typed «تمام»/«إنهاء» at the final review = the ✅ button. Earlier steps
        # (phone, delivery type, address) never take it: they re-show below.
        return _finalize(db, link, session, ui, lines, edit_id=ui.get("msg_id"))

    # awaiting_phone (typed text isn't a phone here) / reuse / final: re-show
    return current_spec(db, link)
