"""Meta Cloud API order notice: request shape, failure isolation, queueing."""

import httpx

from app.chat import callbacks as cb_codec
from app.config import settings
from app.notify import whatsapp_cloud as wc
from app.telegram import service as svc
from tests.test_checkout import _fill_cart
from tests.test_intake import shop  # noqa: F401
from tests.test_menu_buttons_v18 import CHAT, _cb, linked, menu, tap  # noqa: F401
from tests.test_migrations import _alembic, scratch_url  # noqa: F401


def _cfg(monkeypatch):
    monkeypatch.setattr(settings, "whatsapp_token", "tok")
    monkeypatch.setattr(settings, "whatsapp_phone_number_id", "123")


def test_normalize_and_clean():
    assert wc.normalize_phone("01096840957") == "201096840957"
    assert wc.normalize_phone("+20 109 684 0957") == "201096840957"
    assert wc.clean_param("a\nb\t  c") == "a b c"


def test_send_template_shape_and_isolation(monkeypatch):
    _cfg(monkeypatch)
    seen = {}

    def ok(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["json"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.x"}]})

    c = httpx.Client(transport=httpx.MockTransport(ok))
    assert wc.send_template("01096840957", ["ab12", "منى", "2 كرواسون", "استلام"], client=c)
    assert seen["url"].endswith("/123/messages") and seen["auth"] == "Bearer tok"
    j = seen["json"]
    assert j["to"] == "201096840957" and j["template"]["name"] == "order_notice"
    assert len(j["template"]["components"][0]["parameters"]) == 4
    bad = httpx.Client(transport=httpx.MockTransport(
        lambda r: httpx.Response(400, json={"error": {"code": 131030}})))
    assert wc.send_template("01096840957", ["x"], client=bad) is False
    boom = httpx.Client(transport=httpx.MockTransport(
        lambda r: (_ for _ in ()).throw(httpx.ConnectError("x"))))
    assert wc.send_template("01096840957", ["x"], client=boom) is False
    monkeypatch.setattr(settings, "whatsapp_token", "")
    assert wc.send_template("01096840957", ["x"], client=c) is False


def test_order_queues_template_when_enabled(db_session, linked, menu, monkeypatch):  # noqa: F811
    _cfg(monkeypatch)
    monkeypatch.setattr(settings, "cashier_whatsapp_numbers", "01096840957")
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("ckgo"))
    tap(db_session, cb_codec.build("ckdel", "pickup"))
    out = svc.handle_update(db_session, _cb(CHAT, cb_codec.build("ckok")))
    wa = [r for r in out if r.whatsapp_to]
    assert len(wa) == 1 and wa[0].whatsapp_to == "01096840957"
    assert len(wa[0].whatsapp_params) == 4 and "\n" not in "".join(wa[0].whatsapp_params)


def test_disabled_queues_nothing(db_session, linked, menu, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "whatsapp_token", "")
    monkeypatch.setattr(settings, "cashier_whatsapp_numbers", "01096840957")
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("ckgo"))
    tap(db_session, cb_codec.build("ckdel", "pickup"))
    out = svc.handle_update(db_session, _cb(CHAT, cb_codec.build("ckok")))
    assert not [r for r in out if r.whatsapp_to]
