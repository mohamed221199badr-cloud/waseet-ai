"""Cashier WhatsApp alert (CallMeBot sender): normalization, failure isolation, queueing."""

import httpx

from app.chat import callbacks as cb_codec
from app.config import settings
from app.notify import whatsapp
from app.telegram import service as svc
from tests.test_checkout import _fill_cart
from tests.test_intake import shop  # noqa: F401
from tests.test_menu_buttons_v18 import CHAT, _cb, linked, menu, tap  # noqa: F401
from tests.test_migrations import _alembic, scratch_url  # noqa: F401


def test_normalize_phone():
    assert whatsapp.normalize_phone("01096840957") == "+201096840957"
    assert whatsapp.normalize_phone("+20 109 684 0957") == "+201096840957"
    assert whatsapp.normalize_phone("201096840957") == "+201096840957"


def test_send_builds_request_and_never_raises(monkeypatch):
    monkeypatch.setattr(settings, "callmebot_apikey", "k123")
    seen = {}

    def ok(request):
        seen["q"] = dict(request.url.params)
        return httpx.Response(200, text="ok")

    c = httpx.Client(transport=httpx.MockTransport(ok))
    assert whatsapp.send("01096840957", "طلب جديد", client=c)
    assert seen["q"]["phone"] == "+201096840957" and seen["q"]["apikey"] == "k123"
    boom = httpx.Client(transport=httpx.MockTransport(
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("x"))))
    assert whatsapp.send("01096840957", "x", client=boom) is False
    bad = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    assert whatsapp.send("01096840957", "x", client=bad) is False
    monkeypatch.setattr(settings, "callmebot_apikey", "")
    assert whatsapp.send("01096840957", "x", client=c) is False  # no key: skipped


def test_confirmed_order_queues_whatsapp_reply(db_session, linked, menu, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "cashier_whatsapp_numbers", "01096840957")
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("cok"))
    tap(db_session, cb_codec.build("ckdel", "pickup"))
    out = svc.handle_update(db_session, _cb(CHAT, cb_codec.build("ckok")))
    wa = [r for r in out if r.whatsapp_to]
    assert len(wa) == 1 and wa[0].whatsapp_to == "01096840957"
    assert "طلب جديد" in wa[0].text
    assert "جاري التنفيذ" in out[0].text  # the customer's own reply stays first
