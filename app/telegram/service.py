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
import re as _re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.chat import agent as chat_agent
from app.chat import callbacks as cb_codec
from app.chat import menu_buttons, menu_catalog
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
    "ابعت طلبك سطر لكل منتج، مثال:\n١ كيلو مفروم حواوشي\n٢ كيلو كفتة حاتي\n"
    "المحل هيراجع الطلب ويعتمده.\n/stop لفصل الحساب ده عن المحل."
)
MSG_LINKED = "تم ربط الحساب. تقدر تبعت طلبك دلوقتي، سطر لكل منتج."
MSG_ALREADY_LINKED = "الحساب ده مربوط بالفعل. ابعت /stop الأول لو عايز تربطه بكود تاني."
MSG_UNLINKED = "تم فصل الحساب. مش هنستقبل طلبات منه لحد ما تربطه بكود جديد."
MSG_INACTIVE = "الحساب ده متوقف عند المحل. كلّم المحل مباشرة."
MSG_TOO_LONG = "الرسالة طويلة جدًا. قسّمها لرسائل أقصر."
MSG_NO_LINES = "ما لقيتش بنود في الرسالة. اكتب سطر لكل منتج، مثال: ١ كيلو مفروم حواوشي"
MSG_TOO_MANY = "عدد البنود كبير. قسّم الطلب لأكتر من رسالة."


@dataclass
class Reply:
    chat_id: int
    text: str
    buttons: list[tuple[str, str]] | None = None   # (label, callback_data) inline keyboard
    answer_callback_id: str | None = None          # callback_query id to acknowledge
    # UI v1.8 (menu mode): multi-row keyboards and edit-in-place navigation
    button_rows: list[list[tuple[str, str]]] | None = None
    edit_message_id: int | None = None             # bot message to edit instead of sending
    store_msg_id: bool = False                     # worker persists the resulting message id
    photo_file_id: str | None = None               # send this Telegram photo reference

# Pinned callback_data format (slice A, no LLM). Every value matches this whitelist and is
# validated against the linked business's own data before it does anything:
#   cat_<PREFIX>      list a category's items as buttons (PREFIX from the SKU trial map)
#   buy_<product_id>  start ordering that item (same as typing its exact name)
#   pick_<product_id> answer the open "which item did you mean?" question
#   pick_none         none of the offered candidates
#   view_cart         show the current draft without changing it
#   cart_confirm/cart_cancel  same as «تمام»/«الغي», MSG_STALE when no draft is open
#   list_products     same as typing «الأصناف»
# var_<variant_id> from his spec arrives only with Phase 2 menu variants; the Phase 1
# products table has no variant ids, so candidate buttons carry product ids.
_CALLBACK_WHITELIST = _re.compile(
    r"^(cat_[A-Z]{2,5}|buy_[0-9a-fA-F-]{36}|pick_([0-9a-fA-F-]{36}|none)"
    r"|view_cart|cart_confirm|cart_cancel|list_products)$")


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


def _handle_callback(db: Session, cq: dict) -> tuple[int | None, str | None, Reply | None]:
    """One inline-button tap. Returns (chat_id, reply text); both None means ignore.
    For UI v1.8 codec callbacks it returns a full Reply in the third slot instead.
    callback_data is untrusted input: it must match the codec or the whitelist and
    reference the linked business's own data (the agent re-checks ownership)."""
    data = cq.get("data")
    msg = cq.get("message") or {}
    chat = msg.get("chat") or {}
    sender = cq.get("from") or {}
    chat_id = chat.get("id")
    if (chat.get("type") != "private" or not isinstance(chat_id, int)
            or sender.get("is_bot") or not isinstance(data, str)):
        return None, None, None
    link = db.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))
    parsed = cb_codec.parse(data)
    if parsed is not None:
        # UI v1.8 codec: menu-mode businesses only. A legacy-products business never
        # sees these (its keyboards only emit whitelist values).
        if link is None:
            return chat_id, None, Reply(chat_id, MSG_NOT_LINKED,
                                        answer_callback_id=cq.get("id"))
        if not menu_catalog.has_menu(db, link.business_id):
            return chat_id, None, Reply(chat_id, "", answer_callback_id=cq.get("id"))
        msg_id = msg.get("message_id")
        text, rows, photo = menu_buttons.handle_callback(
            db, link, parsed, msg_id if isinstance(msg_id, int) else None)
        rep = Reply(chat_id, text, answer_callback_id=cq.get("id"),
                    edit_message_id=msg_id if isinstance(msg_id, int) else None,
                    store_msg_id=True, photo_file_id=photo)
        if rows:
            rep.button_rows = rows
        return chat_id, None, rep
    if not _CALLBACK_WHITELIST.match(data):
        return chat_id, None, None  # unknown or malformed: acknowledge, do nothing
    if link is None:
        return chat_id, MSG_NOT_LINKED, None
    if data == "cart_confirm":
        return chat_id, chat_agent.handle_confirm(db, link), None
    if data == "cart_cancel":
        return chat_id, chat_agent.handle_cancel(db, link), None
    if data == "view_cart":
        return chat_id, chat_agent.cart_text(db, link), None
    if data == "list_products":
        return chat_id, chat_agent.handle_message(db, link, "الأصناف"), None
    if data == "pick_none":
        return chat_id, chat_agent.handle_skip_choice(db, link), None
    if data.startswith("pick_"):
        return chat_id, chat_agent.handle_pick(db, link, data[len("pick_"):]), None
    if data.startswith("buy_"):
        return chat_id, chat_agent.handle_buy(db, link, data[len("buy_"):]), None
    return chat_id, chat_agent.category_text(db, link, data[len("cat_"):]), None  # cat_


def handle_update(db: Session, update: dict) -> list[Reply]:
    """Process one Telegram update. Commits on success; the caller sends the replies."""
    update_id = update.get("update_id") if isinstance(update, dict) else None
    cq = update.get("callback_query") if isinstance(update, dict) else None
    if isinstance(cq, dict) and isinstance(update_id, int):
        inserted = db.execute(
            pg_insert(TelegramUpdate).values(update_id=update_id)
            .on_conflict_do_nothing().returning(TelegramUpdate.update_id)
        ).first()
        if inserted is None:
            db.rollback()
            return []
        chat_id, text, v18_reply = _handle_callback(db, cq)
        db.commit()
        if chat_id is None:
            return []
        if v18_reply is not None:
            return [v18_reply]
        reply = Reply(chat_id, text or "", answer_callback_id=cq.get("id"))
        if text:
            link = db.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))
            if link is not None:
                reply.buttons = chat_agent.buttons_after(db, link, text)
        return [reply]
    msg = update.get("message") if isinstance(update, dict) else None
    if not isinstance(msg, dict) or not isinstance(update_id, int):
        return []
    chat, sender, text = msg.get("chat") or {}, msg.get("from") or {}, msg.get("text")
    chat_id = chat.get("id")
    if (chat.get("type") != "private" or not isinstance(chat_id, int)
            or sender.get("is_bot")):
        return []
    if not (isinstance(text, str) and text.strip()):
        # UI v1.8 3.0B: a photo is meaningful only while the customer was asked for
        # a menu image; anything else non-text is ignored (spec: never ask otherwise)
        photos = msg.get("photo")
        if isinstance(photos, list) and photos:
            link = db.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))
            if (link is not None and settings.chat_agent_enabled
                    and menu_catalog.has_menu(db, link.business_id)
                    and menu_buttons.wants_photo(db, link)):
                inserted = db.execute(
                    pg_insert(TelegramUpdate).values(update_id=update_id)
                    .on_conflict_do_nothing().returning(TelegramUpdate.update_id)
                ).first()
                if inserted is None:
                    db.rollback()
                    return []
                biggest = max(photos, key=lambda p: (p.get("file_size") or 0,
                                                     p.get("width") or 0))
                file_id = biggest.get("file_id")
                if not isinstance(file_id, str) or not file_id:
                    return []
                out, rows = menu_buttons.handle_photo(db, link, file_id)
                db.commit()
                rep = Reply(chat_id, out, store_msg_id=True)
                if rows:
                    rep.button_rows = rows
                return [rep]
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
        if arg.strip():
            reply = _redeem(db, chat_id, arg)
        elif link is not None and menu_catalog.has_menu(db, link.business_id):
            # UI v1.8 3.0A: the welcome screen is the menu-mode entry point
            text_out, rows = menu_buttons.welcome(db, link)
            db.commit()
            rep = Reply(chat_id, text_out, store_msg_id=True)
            rep.button_rows = rows
            return [rep]
        else:
            reply = MSG_HELP if link else MSG_NOT_LINKED
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
        if menu_catalog.has_menu(db, link.business_id):
            if menu_buttons.wants_text(db, link):
                # UI v1.8 custom-quantity state: this text is a number for the flow
                out, rows, edit_id = menu_buttons.handle_text(db, link, text)
                db.commit()
                rep = Reply(chat_id, out, edit_message_id=edit_id, store_msg_id=True)
                if rows:
                    rep.button_rows = rows
                return [rep]
            reply = chat_agent.handle_message(db, link, text)
            rep = Reply(chat_id, reply)
            # menu mode texts (greeting, help, "الأصناف", typed orders) always carry
            # the v1.8 category keyboard so the button flow is one tap away; the
            # legacy flat buttons belong to the products path only
            rows = menu_buttons.entry_rows(db, link)
            if rows:
                rep.button_rows = rows
            db.commit()
            return [rep]
        reply = chat_agent.handle_message(db, link, text)
        db.commit()
        return [Reply(chat_id, reply, buttons=chat_agent.buttons_after(db, link, reply))]
    else:
        reply = _handle_order(db, link, text)
    db.commit()
    return [Reply(chat_id, reply)]
