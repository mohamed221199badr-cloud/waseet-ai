"""Cashier taps on the new-order notice: ✅ confirm / ❌ reject (two-way confirmation).

Flow: the cashier notice carries two buttons. ✅ moves the order pending -> confirmed,
tells the customer, edits the cashier message (buttons removed) and forwards the order to
the kitchen (items only, no prices, no phone) and to accounts (prices and totals).
❌ asks for a reason with quick buttons, then pending -> rejected and tells the customer.

Safety: only the shop's owner chats and CASHIER_TELEGRAM_CHAT_IDS may tap (checked on the
tap); the order row is locked and the pending check is made inside the lock, so a second
tap (same or another cashier) changes nothing and notifies nobody. No stock deduction:
menu-bot orders use menu variants, not stock products. Send failures never undo the
decision (messages are queued in db.info and sent by the worker after the commit).
"""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.chat import callbacks
from app.chat.menu_agent import _money, _qty
from app.config import settings
from app.models_orders import Customer, Order, OrderLine, TelegramLink

ACTIONS = ("oky", "orj", "orr", "orb")

REASONS = {"na": "الصنف مش متاح حالياً", "far": "العنوان خارج نطاق التوصيل",
           "no": "بدون سبب"}

MSG_DENIED = "مش مسموح لك بالإجراء ده."
MSG_GONE = "الطلب مش موجود."
MSG_DECIDED = "الطلب اتقرر قبل كده ({status})."
_STATUS_AR = {"confirmed": "اتأكد", "rejected": "اترفض"}

MSG_CUSTOMER_OK = "طلبك رقم {oid} اتأكد ✅ وجاري تجهيزه. شكراً ليك ❤️"
MSG_CUSTOMER_NO = ("للأسف طلبك رقم {oid} اتلغى ❌{reason}\n"
                   "تقدر تطلب تاني في أي وقت.")


def _lines(db: Session, order: Order) -> list[OrderLine]:
    return list(db.scalars(select(OrderLine).where(
        OrderLine.order_id == order.id, OrderLine.business_id == order.business_id)
        .order_by(OrderLine.created_at, OrderLine.id)))


def _qty_text(ln: OrderLine) -> str:
    return f"{_qty(ln.quantity)}" + (f" {ln.unit_label}" if ln.unit_label else "")


def kitchen_text(order: Order, lines: list[OrderLine]) -> str:
    """Items only: no prices, no customer phone or address."""
    body = "\n".join(f"• {ln.item_name} ({ln.variant_name}) × {_qty_text(ln)}"
                     for ln in lines)
    kind = "دليفري 🛵" if (order.notes or "").startswith("دليفري") else "استلام 🏪"
    return f"🍳 مطبخ | طلب للتجهيز رقم {str(order.id)[:8]}\n{body}\n{kind}"


def accounts_text(order: Order, lines: list[OrderLine], customer, by: str) -> str:
    """Prices and totals, customer name/phone, who confirmed."""
    body = "\n".join(
        f"• {ln.item_name} ({ln.variant_name}) {_qty_text(ln)} × {_money(ln.unit_price)}"
        f" = {_money(ln.line_total)}ج" for ln in lines)
    who = customer.name if customer is not None else "-"
    phone = f" - {customer.phone}" if customer is not None and customer.phone else ""
    return (f"💰 حسابات | طلب متأكد رقم {str(order.id)[:8]}\nالعميل: {who}{phone}\n{body}\n"
            f"الإجمالي: {_money(order.total)}ج\n{order.notes or ''}\nأكده: {by}").strip()


def notice_rows(order_id) -> list[list[tuple[str, str]]]:
    return [[("✅ تأكيد الطلب", callbacks.build("oky", order_id)),
             ("❌ رفض", callbacks.build("orj", order_id))]]


def allowed(link: TelegramLink | None, chat_id: int) -> bool:
    return (link is not None and link.is_owner) or chat_id in settings.cashier_telegram_chats


def _queue(db: Session, chat_id: int, text: str) -> None:
    db.info.setdefault("order_notify", []).append((chat_id, text))


def handle(db: Session, link: TelegramLink | None, chat_id: int,
           parsed: callbacks.Callback, msg_id: int | None, by: str):
    """-> (text, button_rows, edit). edit=True: append text to the tapped notice and set its
    buttons; False: send text as a short fresh message. Extra messages are queued."""
    if not allowed(link, chat_id):
        return MSG_DENIED, None, False
    order = db.scalar(select(Order).where(Order.id == uuid.UUID(parsed.args[0]))
                      .with_for_update())
    if order is None or (link is not None and link.business_id != order.business_id):
        return MSG_GONE, None, False
    if order.status != "pending":
        return MSG_DECIDED.format(status=_STATUS_AR.get(order.status, order.status)), None, False
    oid = str(order.id)[:8]
    if parsed.action == "orb":  # back from the reason screen: the original buttons
        return "", notice_rows(order.id), True
    if parsed.action == "orj":
        rows = [[(label, callbacks.build("orr", order.id, code))]
                for code, label in REASONS.items()]
        rows.append([("🔙 رجوع", callbacks.build("orb", order.id))])
        return f"سبب رفض الطلب {oid}؟", rows, True
    customer = db.get(Customer, order.customer_id)
    cust_link = db.scalar(select(TelegramLink).where(
        TelegramLink.business_id == order.business_id,
        TelegramLink.customer_id == order.customer_id))
    order.decided_at = datetime.now(timezone.utc)
    if parsed.action == "oky":
        order.status = "confirmed"
        write_audit(db, business_id=order.business_id, entity="order",
                    entity_id=str(order.id), action="confirm",
                    payload={"by_chat": chat_id})
        lines = _lines(db, order)
        if cust_link is not None:
            _queue(db, cust_link.chat_id, MSG_CUSTOMER_OK.format(oid=oid))
        owners = [o.chat_id for o in db.scalars(select(TelegramLink).where(
            TelegramLink.business_id == order.business_id,
            TelegramLink.is_owner.is_(True)))]
        # env list when set, otherwise the owner chats (one person plays every role for
        # now); two clearly labelled messages so the roles stay distinguishable
        for cid in settings.kitchen_telegram_chats or owners:
            _queue(db, cid, kitchen_text(order, lines))
        for cid in settings.accounts_telegram_chats or owners:
            _queue(db, cid, accounts_text(order, lines, customer, by))
        return f"✅ اتأكد الطلب {oid} بواسطة {by}", None, True
    # orr: reject with a reason code
    code = parsed.args[1]
    reason = REASONS[code]
    order.status = "rejected"
    order.rejection_reason = None if code == "no" else reason
    write_audit(db, business_id=order.business_id, entity="order",
                entity_id=str(order.id), action="reject",
                payload={"by_chat": chat_id, "reason": code})
    if cust_link is not None:
        _queue(db, cust_link.chat_id, MSG_CUSTOMER_NO.format(
            oid=oid, reason="" if code == "no" else f"\nالسبب: {reason}"))
    return f"❌ اترفض الطلب {oid} بواسطة {by}", None, True
