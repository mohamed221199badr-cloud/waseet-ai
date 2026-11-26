"""Tiny Telegram Bot API client + long-polling runner.

The token is read from the TELEGRAM_BOT_TOKEN environment variable (or .env) only.
It is never logged: errors are reported by exception type, because HTTP library messages
can contain the request URL, and the URL contains the token.

    python -m app.telegram.client
"""

import logging
import os
import time

import httpx

from app.chat import menu_buttons
from app.config import settings
from app.db import SessionLocal
from app.telegram.service import handle_update

log = logging.getLogger("waseet.telegram")
# httpx logs every request URL at INFO, and the Bot API URL contains the token.
for _name in ("httpx", "httpcore"):
    logging.getLogger(_name).setLevel(logging.WARNING)
API = os.environ.get("TELEGRAM_API_URL", "https://api.telegram.org")  # override: tests only


class TelegramError(Exception):
    pass


class TelegramClient:
    def __init__(self, token: str, http: httpx.Client | None = None):
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is not set")
        self._token = token
        self._http = http or httpx.Client(timeout=40)

    def _call(self, method: str, **params) -> dict:
        try:
            r = self._http.post(f"{API}/bot{self._token}/{method}", json=params)
            data = r.json()
        except Exception as exc:  # never include str(exc): it may hold the URL (and token)
            raise TelegramError(f"{method} failed: {type(exc).__name__}") from None
        if not data.get("ok"):
            raise TelegramError(f"{method} rejected: {data.get('description', 'unknown')[:100]}")
        return data

    def get_updates(self, offset: int | None, timeout: int = 30) -> list[dict]:
        params = {"timeout": timeout, "allowed_updates": ["message", "callback_query"]}
        if offset is not None:
            params["offset"] = offset
        return self._call("getUpdates", **params)["result"]

    @staticmethod
    def _markup(buttons: list[tuple[str, str]] | None,
                button_rows: list[list[tuple[str, str]]] | None) -> dict | None:
        """Inline keyboard payload. button_rows gives explicit rows (pagination,
        quick-quantity grids); flat buttons keep the old one-per-row layout."""
        rows = button_rows if button_rows is not None else (
            [[b] for b in buttons] if buttons else None)
        if not rows:
            return None
        return {"inline_keyboard": [
            [{"text": label, "callback_data": data} for label, data in row] for row in rows]}

    @staticmethod
    def _reply_markup(reply_keyboard: list[list[dict]] | None,
                      remove_keyboard: bool) -> dict | None:
        """ReplyKeyboardMarkup payload for native inputs (contact share). Each row is
        a list of KeyboardButton dicts, e.g. {"text": ..., "request_contact": True}.
        remove_keyboard drops the temporary keyboard once collected."""
        if remove_keyboard:
            return {"remove_keyboard": True}
        if reply_keyboard:
            return {"keyboard": reply_keyboard, "resize_keyboard": True,
                    "one_time_keyboard": True}
        return None

    def send_message(self, chat_id: int, text: str,
                     buttons: list[tuple[str, str]] | None = None,
                     button_rows: list[list[tuple[str, str]]] | None = None,
                     reply_keyboard: list[list[dict]] | None = None,
                     remove_keyboard: bool = False) -> int | None:
        """Sends and returns the new message_id (stored to edit the message later),
        or None when the API reply carried no message object."""
        params: dict = {"chat_id": chat_id, "text": text}
        # one reply_markup per message: an inline keyboard WINS over remove_keyboard
        # (Telegram accepts only one markup object). The contact keyboard is
        # one_time_keyboard, so it hides itself after use; dropping the inline
        # buttons here was the prod bug (19793273) that stranded customers.
        markup = self._markup(buttons, button_rows)
        if markup is None:
            markup = self._reply_markup(reply_keyboard, remove_keyboard)
        if markup:
            params["reply_markup"] = markup
        result = self._call("sendMessage", **params)["result"]
        # mocks and odd API replies may not carry a message object; the caller only
        # stores the id for later edits and works fine without it
        return result["message_id"] if isinstance(result, dict) else None

    def edit_message(self, chat_id: int, message_id: int, text: str,
                     buttons: list[tuple[str, str]] | None = None,
                     button_rows: list[list[tuple[str, str]]] | None = None) -> bool:
        """Edit text + keyboard of one of the bot's own messages. False when Telegram
        refuses (message gone/too old or content unchanged): the caller then sends a
        fresh message instead."""
        params: dict = {"chat_id": chat_id, "message_id": message_id, "text": text}
        markup = self._markup(buttons, button_rows)
        if markup:
            params["reply_markup"] = markup
        try:
            self._call("editMessageText", **params)
            return True
        except TelegramError as exc:
            if "message is not modified" in str(exc) or "message can't be edited" in str(exc) \
                    or "message to edit not found" in str(exc) \
                    or "no text in the message" in str(exc):
                # also: the target is a photo message (per-category pages, m014),
                # which has no editable text - the caller sends fresh instead
                return False
            raise

    def send_photo(self, chat_id: int, file_id: str,
                   button_rows: list[list[tuple[str, str]]] | None = None,
                   caption: str | None = None) -> int | None:
        """Sends a photo by its Telegram file_id reference (no bytes stored by us).
        The caption carries the screen's text (Telegram caps captions at 1024)."""
        params: dict = {"chat_id": chat_id, "photo": file_id}
        if caption:
            params["caption"] = caption[:1024]
        markup = self._markup(None, button_rows)
        if markup:
            params["reply_markup"] = markup
        result = self._call("sendPhoto", **params)["result"]
        return result["message_id"] if isinstance(result, dict) else None

    def answer_callback_query(self, callback_query_id: str) -> None:
        """Stops the client's loading spinner; no text shown to the customer."""
        self._call("answerCallbackQuery", callback_query_id=callback_query_id)


def process_batch(client: TelegramClient, updates: list[dict], session_factory=SessionLocal) -> int:
    """Handle updates in order. Returns the next offset. A failing update is logged and
    skipped (its transaction is rolled back) so one bad message cannot block the queue."""
    offset = 0
    for upd in updates:
        offset = max(offset, upd.get("update_id", 0) + 1)
        db = session_factory()
        try:
            replies = handle_update(db, upd)
        except Exception as exc:
            db.rollback()
            log.error("update %s failed: %s", upd.get("update_id"), type(exc).__name__)
            continue
        finally:
            db.close()
        for rep in replies:
            try:
                if rep.answer_callback_id is not None:
                    client.answer_callback_query(rep.answer_callback_id)
                if rep.photo_file_id:
                    # photo screens (category pages, official menu): the text rides
                    # as the caption, one message with the buttons under the photo
                    mid = client.send_photo(rep.chat_id, rep.photo_file_id,
                                            rep.button_rows, caption=rep.text or None)
                    if rep.store_msg_id and mid is not None:
                        db2 = session_factory()
                        try:
                            menu_buttons.set_msg_id(db2, rep.chat_id, mid)
                            db2.commit()
                        finally:
                            db2.close()
                elif rep.text:
                    mid = None
                    if rep.edit_message_id is not None:
                        edited = client.edit_message(
                            rep.chat_id, rep.edit_message_id, rep.text,
                            rep.buttons, rep.button_rows)
                        if edited:
                            mid = rep.edit_message_id
                    if mid is None:  # no edit target or Telegram refused: send fresh
                        mid = client.send_message(
                            rep.chat_id, rep.text, rep.buttons, rep.button_rows,
                            rep.reply_keyboard, rep.remove_keyboard)
                    if rep.store_msg_id and mid is not None:
                        # the flow edits this message on the next tap; keep its id
                        db2 = session_factory()
                        try:
                            menu_buttons.set_msg_id(db2, rep.chat_id, mid)
                            db2.commit()
                        finally:
                            db2.close()
            except TelegramError as exc:
                log.error("send failed: %s", exc)
    return offset


def run() -> None:
    logging.basicConfig(level=logging.INFO)
    client = TelegramClient(settings.telegram_bot_token)
    offset = None
    log.info("polling started")
    while True:
        try:
            updates = client.get_updates(offset)
        except TelegramError as exc:
            log.error("%s", exc)
            time.sleep(5)
            continue
        if updates:
            offset = process_batch(client, updates)


if __name__ == "__main__":
    run()
