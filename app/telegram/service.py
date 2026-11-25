"""Telegram intake logic. Pure over (db session, update dict): no network, no token.

Security rules:
- Only private chats are served; groups/channels are ignored.
- A chat reaches a business ONLY through a row in telegram_links, created by redeeming a
  one-time code that the business owner generated (JWT-protected endpoint) for one customer.
  The business and customer always come from that row, never from message text.
- chat_id is unique system-wide: one chat cannot be linked to two customers or tenants,
  and a linked chat cannot be silently re-linked (send /stop first).
- An unlinked chat gets one fixed reply and learns nothing about any business.
- The bot never replies with other customers' data. Prices/stock warnings appear only in the
  chat-agent confirmation (own customer price, from the DB; CHAT_SHOW_PRICES=false removes them).
- Orders created here are drafts. Confirmation stays with the owner.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.chat import agent as chat_agent
from app.config import settings
from app.models_orders import (
    Customer,
    TelegramLink,
    TelegramLinkCode,
    TelegramUpdate,
)
from app.repositories import intake as intake_repo

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # no 0/O/1/I
CODE_LENGTH = 10
MAX_TEXT = 5000

MSG_NOT_LINKED = (
    "الحساب ده مش مربوط بأي محل. اطلب من المحل كود ربط وابعته هنا بالشكل ده: /start الكود"
)
MSG_BAD_CODE = "الكود غير صحيح أو منتهي أو اتستخدم قبل كده. اطلب كود جديد من المحل."
MSG_HELP = (
    "ابعت طلبك سطر لكل منتج، مثال:\n٣ سكر\n٢ ملح\nالمحل هيراجع الطلب ويعتمده.\n"
    "/stop لفصل الحساب ده عن المحل."
)
MSG_LINKED = "تم ربط الحساب. تقدر تبعت طلبك دلوقتي، سطر لكل منتج."
MSG_ALREADY_LINKED = "الحساب ده مربوط بالفعل. ابعت /stop الأول لو عايز تربطه بكود تاني."
MSG_UNLINKED = "تم فصل الحساب. مش هنستقبل طلبات منه لحد ما تربطه بكود جديد."
MSG_INACTIVE = "الحساب ده متوقف عند المحل. كلّم المحل مباشرة."
MSG_TOO_LONG = "الرسالة طويلة جدًا. قسّمها لرسائل أقصر."
MSG_NO_LINES = "ما لقيتش بنود في الرسالة. اكتب سطر لكل منتج، مثال: ٣ سكر"
MSG_TOO_MANY = "عدد البنود كبير. قسّم الطلب لأكتر من رسالة."


@dataclass
class Reply:
    chat_id: int
    text: str


def hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().upper().encode()).hexdigest()


def new_link_code(db: Session, business_id, customer_id) -> tuple[str, TelegramLinkCode]:
    code = "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))
    row = TelegramLinkCode(
        business_id=business_id, customer_id=customer_id, code_hash=hash_code(code),
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None)
        + timedelta(hours=settings.telegram_link_code_ttl_hours),
    )
    db.add(row)
    db.flush()
    return code, row


def _redeem(db: Session, chat_id: int, code: str) -> str:
    if db.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id)) is not None:
        return MSG_ALREADY_LINKED
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    row = db.scalar(
        select(TelegramLinkCode)
        .where(TelegramLinkCode.code_hash == hash_code(code))
        .with_for_update()
    )
    if row is None or row.used_at is not None or row.expires_at < now:
        return MSG_BAD_CODE
    customer = db.scalar(
        select(Customer).where(
            Customer.business_id == row.business_id, Customer.id == row.customer_id
        )
    )
    if customer is None or not customer.is_active:
        return MSG_BAD_CODE
    row.used_at = now
    db.add(TelegramLink(business_id=row.business_id, customer_id=row.customer_id, chat_id=chat_id))
    write_audit(
        db, business_id=row.business_id, entity="telegram_link", entity_id=str(row.customer_id),
        action="create", payload={"customer_id": str(row.customer_id)},
    )
    return MSG_LINKED


def _order_reply(order, results) -> str:
    ok = [r.raw for r in results if r.status == "matched"]
    bad = [r.raw for r in results if r.status != "matched"]
    lines = [f"استلمنا طلبك (رقم {str(order.id)[:8]}). لسه مسودة لحد ما المحل يعتمده."]
    if ok:
        lines.append(f"بنود واضحة ({len(ok)}):")
        lines += [f"✓ {t}" for t in ok]
    if bad:
        lines.append(f"بنود المحل هيراجعها معاك ({len(bad)}):")
        lines += [f"؟ {t}" for t in bad]
    return "\n".join(lines)


def _handle_order(db: Session, link: TelegramLink, text: str) -> str:
    if len(text) > MAX_TEXT:
        return MSG_TOO_LONG
    customer = db.scalar(
        select(Customer).where(
            Customer.business_id == link.business_id, Customer.id == link.customer_id
        )
    )
    if customer is None or not customer.is_active:
        return MSG_INACTIVE
    try:
        order, results = intake_repo.create_draft_from_text(
            db, link.business_id, customer, text, "telegram"
        )
    except ValueError:
        return MSG_TOO_MANY
    if not results:
        db.rollback()  # drop the empty draft
        return MSG_NO_LINES
    matched = sum(1 for r in results if r.status == "matched")
    write_audit(
        db, business_id=link.business_id, entity="order", entity_id=str(order.id),
        action="create",
        payload={"source": "telegram_intake", "channel": "telegram", "lines": len(results),
                 "matched": matched, "exceptions": len(results) - matched},
    )
    return _order_reply(order, results)


def handle_update(db: Session, update: dict) -> list[Reply]:
    """Process one Telegram update. Commits on success; the caller sends the replies."""
    msg = update.get("message") if isinstance(update, dict) else None
    update_id = update.get("update_id") if isinstance(update, dict) else None
    if not isinstance(msg, dict) or not isinstance(update_id, int):
        return []
    chat, sender, text = msg.get("chat") or {}, msg.get("from") or {}, msg.get("text")
    chat_id = chat.get("id")
    if (chat.get("type") != "private" or not isinstance(chat_id, int)
            or sender.get("is_bot") or not isinstance(text, str) or not text.strip()):
        return []
    # Redelivered update: do nothing. Same transaction as the work, so a crash retries cleanly.
    inserted = db.execute(
        pg_insert(TelegramUpdate).values(update_id=update_id)
        .on_conflict_do_nothing().returning(TelegramUpdate.update_id)
    ).first()
    if inserted is None:
        db.rollback()
        return []

    text = text.strip()
    cmd, _, arg = text.partition(" ")
    cmd = cmd.split("@")[0].lower()  # "/start@MyBot"
    link = db.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))

    if cmd == "/start":
        reply = _redeem(db, chat_id, arg) if arg.strip() else (MSG_HELP if link else MSG_NOT_LINKED)
    elif cmd == "/help":
        reply = MSG_HELP if link else MSG_NOT_LINKED
    elif cmd == "/stop":
        if link is None:
            reply = MSG_NOT_LINKED
        else:
            db.delete(link)
            write_audit(
                db, business_id=link.business_id, entity="telegram_link",
                entity_id=str(link.customer_id), action="delete", payload={"by": "customer"},
            )
            reply = MSG_UNLINKED
    elif link is None:
        reply = MSG_NOT_LINKED
    elif cmd.startswith("/"):
        reply = MSG_HELP
    elif settings.chat_agent_enabled:
        reply = chat_agent.handle_message(db, link, text)
    else:
        reply = _handle_order(db, link, text)
    db.commit()
    return [Reply(chat_id, reply)]
