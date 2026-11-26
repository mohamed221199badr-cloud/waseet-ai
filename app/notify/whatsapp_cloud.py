"""Order notice to a WhatsApp number through Meta's official Cloud API.

Business-initiated messages need an approved template, so the notice is a template
send (template name/language from env). Secrets (token, phone number id) come from
env only, never code or chat. A failure is logged and swallowed: it must never block
or fail the customer's order. Not wired unless WHATSAPP_TOKEN and
WHATSAPP_PHONE_NUMBER_ID are both set.
"""

import logging
import re

import httpx

from app.config import settings

log = logging.getLogger("waseet.whatsapp")
GRAPH = "https://graph.facebook.com"


def normalize_phone(raw: str) -> str:
    """01096840957 -> 201096840957 (digits only, country code, no +)."""
    digits = re.sub(r"\D", "", raw)
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("0") and len(digits) == 11:
        digits = "20" + digits[1:]
    return digits


def clean_param(value: str) -> str:
    """Template variables cannot hold newlines/tabs or runs of 4+ spaces."""
    return re.sub(r"\s+", " ", str(value)).strip()[:900] or "-"


def enabled() -> bool:
    return bool(settings.whatsapp_token and settings.whatsapp_phone_number_id)


def send_template(phone: str, params: list[str],
                  client: httpx.Client | None = None) -> bool:
    """One template message with body variables. True on 2xx; never raises."""
    if not enabled():
        log.warning("WhatsApp notice skipped: WHATSAPP_TOKEN/PHONE_NUMBER_ID not set")
        return False
    own = client is None
    http = client or httpx.Client(timeout=10.0)
    url = f"{GRAPH}/{settings.whatsapp_api_version}/{settings.whatsapp_phone_number_id}/messages"
    body = {
        "messaging_product": "whatsapp",
        "to": normalize_phone(phone),
        "type": "template",
        "template": {
            "name": settings.whatsapp_template_name,
            "language": {"code": settings.whatsapp_template_lang},
            "components": [{"type": "body", "parameters": [
                {"type": "text", "text": clean_param(p)} for p in params]}],
        },
    }
    try:
        r = http.post(url, json=body,
                      headers={"Authorization": f"Bearer {settings.whatsapp_token}"})
        if r.status_code // 100 != 2:
            code = ""
            try:
                code = str(r.json().get("error", {}).get("code", ""))
            except Exception:
                pass
            log.error("WhatsApp notice failed: HTTP %s code %s", r.status_code, code)
            return False
        return True
    except Exception as exc:  # no str(exc): may hold the URL
        log.error("WhatsApp notice failed: %s", type(exc).__name__)
        return False
    finally:
        if own:
            http.close()
