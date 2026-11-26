"""Cashier WhatsApp alert through CallMeBot (free, personal-use API).

CallMeBot only delivers to a number that activated it and holds its own apikey, so
this is the test/stop-gap sender; the official Meta Cloud API replaces it later.
The apikey comes from the CALLMEBOT_APIKEY env var (never in code or chat). A failure
is logged and swallowed: it must never block or fail the customer's order.
"""

import logging
import re

import httpx

from app.config import settings

log = logging.getLogger("waseet.whatsapp")
API_URL = "https://api.callmebot.com/whatsapp.php"


def normalize_phone(raw: str) -> str:
    """01096840957 -> +201096840957; 201096840957 / +201096840957 stay as +20..."""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        digits = "20" + digits[1:]
    return "+" + digits


def send(phone: str, text: str, client: httpx.Client | None = None) -> bool:
    """One text message. True on a 2xx answer; never raises."""
    key = settings.callmebot_apikey
    if not key:
        log.warning("cashier WhatsApp skipped: CALLMEBOT_APIKEY is not set")
        return False
    own = client is None
    http = client or httpx.Client(timeout=10.0)
    try:
        r = http.get(API_URL, params={"phone": normalize_phone(phone), "text": text,
                                      "apikey": key})
        if r.status_code // 100 != 2:
            log.error("cashier WhatsApp failed: HTTP %s", r.status_code)
            return False
        return True
    except Exception as exc:  # network, timeout: the order is already saved
        log.error("cashier WhatsApp failed: %s", type(exc).__name__)  # no URL: has the key
        return False
    finally:
        if own:
            http.close()
