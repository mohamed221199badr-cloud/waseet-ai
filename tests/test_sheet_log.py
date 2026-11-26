"""Google Sheet order log: request shape, failure isolation, queued once per confirmed order."""

import json

import httpx

from app.chat import callbacks as cb_codec
from app.config import settings
from app.notify import sheet_log
from app.telegram import service as svc
from tests.test_checkout import _fill_cart
from tests.test_intake import shop  # noqa: F401
from tests.test_menu_buttons_v18 import CHAT, _cb, linked, menu, tap  # noqa: F401
from tests.test_migrations import _alembic, scratch_url  # noqa: F401

ROW = {"time": "2026-11-26 12:00", "order_id": "ab12cd34", "customer": "منى", "phone": "",
       "method": "استلام", "address": "", "items": "x × 1", "total": "50", "by": "تلقائي"}


def _cfg(monkeypatch):
    monkeypatch.setattr(settings, "sheet_webhook_url", "https://script.example/exec")
    monkeypatch.setattr(settings, "sheet_webhook_token", "tok")


def test_append_row_shape_retry_and_isolation(monkeypatch):
    _cfg(monkeypatch)
    seen = []

    def ok(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    assert sheet_log.append_row(ROW, client=httpx.Client(transport=httpx.MockTransport(ok)))
    assert seen[0] == {"token": "tok", "row": ROW}
    calls = []

    def flaky(request):
        calls.append(1)
        return httpx.Response(500) if len(calls) == 1 else httpx.Response(200, json={"ok": True})

    assert sheet_log.append_row(ROW, client=httpx.Client(transport=httpx.MockTransport(flaky)))
    assert len(calls) == 2
    bad = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"ok": False, "error": "auth"})))
    assert sheet_log.append_row(ROW, client=bad) is False
    boom = httpx.Client(transport=httpx.MockTransport(
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("x"))))
    assert sheet_log.append_row(ROW, client=boom) is False
    monkeypatch.setattr(settings, "sheet_webhook_token", "")
    assert sheet_log.append_row(ROW) is False


def test_confirmed_order_queues_one_row(db_session, linked, menu, monkeypatch):  # noqa: F811
    _cfg(monkeypatch)
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("ckgo"))
    tap(db_session, cb_codec.build("ckdel", "pickup"))
    out = svc.handle_update(db_session, _cb(CHAT, cb_codec.build("ckok")))
    rows = [r.sheet_row for r in out if r.sheet_row]
    assert len(rows) == 1
    r = rows[0]
    assert r["method"] == "استلام" and r["items"] and r["total"] and len(r["order_id"]) == 8


def test_disabled_queues_nothing(db_session, linked, menu, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "sheet_webhook_url", "")
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("ckgo"))
    tap(db_session, cb_codec.build("ckdel", "pickup"))
    out = svc.handle_update(db_session, _cb(CHAT, cb_codec.build("ckok")))
    assert not [r for r in out if r.sheet_row]
