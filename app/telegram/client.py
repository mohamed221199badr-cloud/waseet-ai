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

    def send_message(self, chat_id: int, text: str,
                     buttons: list[tuple[str, str]] | None = None) -> None:
        params: dict = {"chat_id": chat_id, "text": text}
        if buttons:
            params["reply_markup"] = {"inline_keyboard": [
                [{"text": label, "callback_data": data}] for label, data in buttons]}
        self._call("sendMessage", **params)

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
                if rep.text:
                    client.send_message(rep.chat_id, rep.text, rep.buttons)
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
