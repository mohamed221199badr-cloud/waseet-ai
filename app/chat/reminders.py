"""Reminders for orders still pending (nobody pressed ✅/❌): after 3 and after 10 minutes
the cashier chats get the notice again with the same buttons.

No schema change: a sent reminder is an audit_events row (entity=order, action
remind1/remind2), which is also the dedupe. Only orders created in the last 30 minutes are
considered, so old pending orders never produce a burst after a deploy or restart.
Called by the worker loop between polls; a failure here never stops the bot.
"""

from sqlalchemy import String, cast, exists, func, select, text
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.chat import cashier_actions
from app.config import settings
from app.models import AuditEvent
from app.models_orders import Customer, Order, TelegramLink

STEPS = ((3, "remind1"), (10, "remind2"))
WINDOW_MINUTES = 30


def _already(action: str):
    return exists().where(AuditEvent.entity == "order", AuditEvent.action == action,
                          AuditEvent.entity_id == cast(Order.id, String))


def due(db: Session) -> list[tuple[int, str, list]]:
    """Queue-and-record: returns (chat_id, text, rows) to send; commits the audit rows."""
    out: list[tuple[int, str, list]] = []
    for minutes, action in STEPS:
        orders = db.scalars(select(Order).where(
            Order.status == "pending",
            Order.created_at <= func.now() - text(f"interval '{minutes} minutes'"),
            Order.created_at >= func.now() - text(f"interval '{WINDOW_MINUTES} minutes'"),
            ~_already(action)).order_by(Order.created_at)).all()
        for order in orders:
            write_audit(db, business_id=order.business_id, entity="order",
                        entity_id=str(order.id), action=action, payload={"after_min": minutes})
            owners = [o.chat_id for o in db.scalars(select(TelegramLink).where(
                TelegramLink.business_id == order.business_id,
                TelegramLink.is_owner.is_(True)))]
            customer = db.get(Customer, order.customer_id)
            who = customer.name if customer is not None else "-"
            msg = (f"⏰ طلب رقم {str(order.id)[:8]} لسه مستني تأكيدك "
                   f"(من {minutes} دقايق)\nالعميل: {who} | الإجمالي: "
                   f"{cashier_actions._money(order.total)}ج")
            for cid in dict.fromkeys(owners + settings.cashier_telegram_chats):
                out.append((cid, msg, cashier_actions.notice_rows(order.id)))
    db.commit()
    return out
