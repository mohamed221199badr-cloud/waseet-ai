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

import re as _re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.chat import agent as chat_agent
from app.chat import callbacks as cb_codec
from app.chat import cashier_actions, checkout, menu_buttons, menu_catalog
from app.config import settings
from app.models_orders import (
    Customer,
    TelegramLink,
    TelegramUpdate,
)
from app.repositories import intake as intake_repo

MAX_TEXT = 5000

MSG_NOT_LINKED = "البوت مش متاح للحساب ده دلوقتي. كلّم المحل مباشرة."
MSG_HELP = (
    "ابعت طلبك سطر لكل منتج، مثال:\n١ كيلو مفروم حواوشي\n٢ كيلو كفتة حاتي\n"
    "المحل هيراجع الطلب ويعتمده.\n/stop لفصل الحساب ده عن المحل."
)
MSG_ALREADY_LINKED = "الحساب ده مربوط بالفعل. ابعت /stop الأول لو عايز تربطه بكود تاني."
MSG_UNLINKED = "تم فصل الحساب. ابعت أي رسالة لو عايز تطلب تاني."
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
    # onboarding (spec 19758722 F2): native reply keyboard (contact share) / remove it
    reply_keyboard: list[list[dict]] | None = None
    remove_keyboard: bool = False



def _checkout_reply(chat_id: int, spec: dict,
                    answer_callback_id: str | None = None) -> Reply:
    """A checkout step dict as a Reply (checkout spec 2026-11-26)."""
    rep = Reply(chat_id, spec["text"], answer_callback_id=answer_callback_id,
                store_msg_id=True, reply_keyboard=spec.get("reply_keyboard"),
                remove_keyboard=spec.get("remove_keyboard", False),
                edit_message_id=spec.get("edit_message_id"))
    if spec.get("rows"):
        rep.button_rows = spec["rows"]
    return rep

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


def _menu_business(db: Session):
    """The single business this bot serves: the unique business that has a menu.
    None when zero or several qualify - auto-link stays off and codes are needed."""
    from app.models import Business
    candidates = [b for b in db.scalars(select(Business)).all()
                  if menu_catalog.has_menu(db, b.id)]
    return candidates[0] if len(candidates) == 1 else None


def _auto_link(db: Session, chat_id: int, sender: dict) -> TelegramLink | None:
    """Find-or-create the customer for an unlinked chat on the single menu business,
    and link it. A deactivated customer is never re-activated (the owner's kill
    switch for abuse). Returns None when auto-link is off or the customer is off."""
    business = _menu_business(db)
    if business is None:
        return None
    customer = db.scalar(select(Customer).where(
        Customer.business_id == business.id, Customer.telegram_chat_id == chat_id))
    if customer is None:
        name = (sender.get("first_name") or sender.get("username")
                or f"عميل {chat_id}")
        customer = Customer(business_id=business.id, name=str(name)[:200],
                            telegram_chat_id=chat_id)
        db.add(customer)
        db.flush()
    if not customer.is_active:
        return None
    link = TelegramLink(business_id=business.id, customer_id=customer.id,
                        chat_id=chat_id)
    db.add(link)
    write_audit(db, business_id=business.id, entity="telegram_link",
                entity_id=str(customer.id), action="create",
                payload={"customer_id": str(customer.id), "by": "auto"})
    db.flush()
    return link


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
    if parsed is not None and parsed.action in cashier_actions.ACTIONS:
        # cashier taps work for env-listed chats that have no link: handled first
        who = sender.get("first_name") or sender.get("username") or str(chat_id)
        msg_id = msg.get("message_id")
        text, rows, edit = cashier_actions.handle(db, link, chat_id, parsed, msg_id, who)
        if edit and isinstance(msg_id, int):
            orig = (msg.get("text") or "").split("\n\n⟫ ")[0]
            shown = f"{orig}\n\n⟫ {text}" if text else orig
            rep = Reply(chat_id, shown, answer_callback_id=cq.get("id"),
                        edit_message_id=msg_id)
            rep.button_rows = rows or []
        else:
            rep = Reply(chat_id, text, answer_callback_id=cq.get("id"))
        return chat_id, None, rep
    if link is None and settings.chat_agent_enabled:
        link = _auto_link(db, chat_id, sender)
        if link is not None:
            # first contact is a tap: the main menu, fresh message either way
            text, rows = menu_buttons.main_menu(db, link)
            rep = Reply(chat_id, text, answer_callback_id=cq.get("id"),
                        store_msg_id=True)
            rep.button_rows = rows
            return chat_id, None, rep
    parsed = cb_codec.parse(data)
    if parsed is not None:
        # UI v1.8 codec: menu-mode businesses only. A legacy-products business never
        # sees these (its keyboards only emit whitelist values).
        if link is None:
            return chat_id, None, Reply(chat_id, MSG_NOT_LINKED,
                                        answer_callback_id=cq.get("id"))
        if not menu_catalog.has_menu(db, link.business_id):
            return chat_id, None, Reply(chat_id, "", answer_callback_id=cq.get("id"))
        if parsed.action in checkout.CALLBACK_ACTIONS:
            # checkout taps (cart confirm starts it): checkout renders the step,
            # editing the tapped message except where a reply keyboard is needed
            msg_id = msg.get("message_id")
            spec = checkout.handle_callback(
                db, link, parsed, msg_id if isinstance(msg_id, int) else None)
            return chat_id, None, _checkout_reply(chat_id, spec, cq.get("id"))
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
    """Process one Telegram update. Commits on success; the caller sends the replies.
    A confirmed order also queues a message to the owner/cashier chats (checkout
    _finalize puts it in db.info); it is sent after the customer's own reply."""
    db.info.pop("order_notify", None)
    replies = _handle_update(db, update)
    for item in db.info.pop("order_notify", []):
        chat_id, text = item[0], item[1]
        rep = Reply(chat_id, text)
        if len(item) > 2:
            rep.button_rows = item[2]
        replies.append(rep)
    return replies


def _handle_update(db: Session, update: dict) -> list[Reply]:
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
        # checkout: a natively shared contact while the phone step is open
        contact = msg.get("contact")
        if isinstance(contact, dict) and settings.chat_agent_enabled:
            link = db.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))
            if (link is not None and menu_catalog.has_menu(db, link.business_id)
                    and checkout.awaiting_contact(db, link)):
                inserted = db.execute(
                    pg_insert(TelegramUpdate).values(update_id=update_id)
                    .on_conflict_do_nothing().returning(TelegramUpdate.update_id)
                ).first()
                if inserted is None:
                    db.rollback()
                    return []
                spec = checkout.handle_contact(db, link, contact, sender.get("id"))
                db.commit()
                return [_checkout_reply(chat_id, spec)]
        # UI v1.8 3.0B: a photo is meaningful only while the customer was asked for
        # a menu image; anything else non-text is ignored (spec: never ask otherwise)
        photos = msg.get("photo")
        if isinstance(photos, list) and photos:
            link = db.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))
            if (link is not None and settings.chat_agent_enabled
                    and menu_catalog.has_menu(db, link.business_id)):
                biggest = max(photos, key=lambda p: (p.get("file_size") or 0,
                                                     p.get("width") or 0))
                file_id = biggest.get("file_id")
                if not isinstance(file_id, str) or not file_id:
                    return []
                caption = (msg.get("caption") or "").strip()
                # owner page-photo upload (m014): caption = the category name. The
                # prompted menu-image flow wins when it is open.
                owner_caption = (link.is_owner and caption
                                 and not menu_buttons.wants_photo(db, link))
                if menu_buttons.wants_photo(db, link) or owner_caption:
                    inserted = db.execute(
                        pg_insert(TelegramUpdate).values(update_id=update_id)
                        .on_conflict_do_nothing().returning(TelegramUpdate.update_id)
                    ).first()
                    if inserted is None:
                        db.rollback()
                        return []
                    if owner_caption:
                        out, rows = menu_buttons.handle_category_photo(
                            db, link, caption, file_id)
                    else:
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

    _fresh_auto_link = False
    if link is None and settings.chat_agent_enabled:
        # unlinked chat on the single-shop bot: auto-link to the menu business and
        # enter through the welcome screen (19672361); codes and /stop still work
        link = _auto_link(db, chat_id, sender)
        _fresh_auto_link = link is not None
    if cmd == "/start":
        # "/start <anything>" (old link codes) is ignored: same welcome as plain /start
        if link is not None and menu_catalog.has_menu(db, link.business_id):
            # no onboarding gate (checkout spec 2026-11-26): the main menu directly
            text_out, rows = menu_buttons.main_menu(db, link)
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
        reply = MSG_NOT_LINKED  # auto-link off (no unique menu business) or customer off
    elif cmd.startswith("/"):
        reply = MSG_HELP
    elif settings.chat_agent_enabled:
        if link is not None and _fresh_auto_link:
            text_out, rows = menu_buttons.main_menu(db, link)
            db.commit()
            rep = Reply(chat_id, text_out, store_msg_id=True)
            rep.button_rows = rows
            return [rep]
        if menu_catalog.has_menu(db, link.business_id):
            if checkout.active(db, link):
                # checkout open: this text answers the current step (address text,
                # typed delivery answer, typed cancel); anything else re-shows it
                spec = checkout.handle_text(db, link, text)
                db.commit()
                return [_checkout_reply(chat_id, spec)]
            if menu_buttons.wants_text(db, link):
                # UI v1.8 custom-quantity state: this text is a number for the flow
                out, rows, edit_id = menu_buttons.handle_text(db, link, text)
                db.commit()
                rep = Reply(chat_id, out, edit_message_id=edit_id, store_msg_id=True)
                if rows:
                    rep.button_rows = rows
                return [rep]
            if menu_buttons.is_bare_number(text):
                # a typed quantity with no numpad open (bug 19817564): the matcher
                # would only shrug, so point him back at picking the item first.
                # A typed-flow quantity question (clarifying/confirming) still owns
                # its numbers, so the hint fires only with no open order state.
                sess = chat_agent._load(db, link)
                if sess is None or sess.state not in ("clarifying", "confirming"):
                    rep = Reply(chat_id, menu_buttons.MSG_PICK_ITEM_FIRST)
                    rows = menu_buttons.entry_rows(db, link)
                    if rows:
                        rep.button_rows = rows
                    db.commit()
                    return [rep]
            reply = chat_agent.handle_message(db, link, text)
            if checkout.active(db, link):
                # a typed «تمام» on an open confirmation just started checkout:
                # render the first step (with its keyboard) instead of plain text
                spec = checkout.current_spec(db, link)
                db.commit()
                return [_checkout_reply(chat_id, spec)]
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
