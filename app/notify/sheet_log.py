"""Every confirmed order is appended as one row to a Google Sheet.

Transport: a small Google Apps Script web app that belongs to the owner's Sheet (no
Google Cloud project, no service-account key). This module sends one JSON POST with a
shared token. Both values come from env only (SHEET_WEBHOOK_URL, SHEET_WEBHOOK_TOKEN).
The script de-duplicates on the order id, so a retry never doubles a row. A failure is
logged by type/status only and never blocks the order or the Telegram replies.
Setup script: docs/google_sheet_script.gs
"""

import logging

import httpx

from app.config import settings

log = logging.getLogger("waseet.sheet")


def enabled() -> bool:
    return bool(settings.sheet_webhook_url and settings.sheet_webhook_token)


def append_row(row: dict, client: httpx.Client | None = None) -> bool:
    """POST one order row (one retry). True on a 2xx answer carrying ok; never raises."""
    if not enabled():
        return False
    own = client is None
    http = client or httpx.Client(timeout=15.0, follow_redirects=True)  # Apps Script redirects
    try:
        for attempt in (1, 2):
            try:
                r = http.post(settings.sheet_webhook_url,
                              json={"token": settings.sheet_webhook_token, "row": row})
                if r.status_code // 100 == 2 and '"ok":true' in r.text.replace(" ", ""):
                    return True
                log.error("sheet log failed: HTTP %s (try %s)", r.status_code, attempt)
            except Exception as exc:  # no str(exc): it holds the URL (a secret)
                log.error("sheet log failed: %s (try %s)", type(exc).__name__, attempt)
        return False
    finally:
        if own:
            http.close()
