"""Exceptions screen: the API client against the real app, and the Streamlit script run
headless with Streamlit's AppTest against that same app (no browser, no real server)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from tests.conftest import TEST_PASSWORD
from tests.test_intake import shop  # noqa: F401
from ui import api_client

APP = str(Path(__file__).resolve().parent.parent / "ui" / "app.py")
BASE = "http://testserver/api/v1"


@pytest.fixture()
def api(client, shop):  # noqa: F811
    a = api_client.ApiClient(BASE, http=client)
    a.login("test-biz-a", "a@test.local", TEST_PASSWORD)
    return a


def make_draft(client, shop_, text):  # noqa: F811
    r = client.post("/api/v1/intake/text-order", headers=shop_["h"], json={
        "text": text, "customer_id": shop_["c"]["id"], "channel": "telegram"})
    assert r.status_code == 201, r.text
    return r.json()["order"]


def test_client_full_flow(client, shop, api):  # noqa: F811
    order = make_draft(client, shop, "٣ سكر\n٣ رز\n٥ بسكويت")
    drafts = api.orders("draft")
    assert len(drafts) == 1 and len(drafts[0]["exceptions"]) == 2
    amb = next(e for e in drafts[0]["exceptions"] if e["reason"] == "ambiguous")
    other = next(e for e in drafts[0]["exceptions"] if e["reason"] == "not_found")
    with pytest.raises(api_client.ApiError) as ei:
        api.submit(order["id"])
    assert ei.value.status == 409
    api.resolve(order["id"], amb["id"], shop["p"]["R5"]["id"])
    api.dismiss(order["id"], other["id"])
    assert api.submit(order["id"])["status"] == "pending"
    assert api.confirm(order["id"])["status"] == "confirmed"
    assert api.orders("confirmed")[0]["id"] == order["id"]


def test_client_errors(client, shop, api):  # noqa: F811
    with pytest.raises(api_client.ApiError) as ei:
        api_client.ApiClient(BASE, http=client).customers()
    assert ei.value.status == 401
    with pytest.raises(api_client.ApiError) as ei:
        api_client.ApiClient(BASE, http=client).login("test-biz-a", "a@test.local", "wrong")
    assert ei.value.status == 401
    code = api.new_link_code(shop["c"]["id"])
    assert len(code["code"]) == 10
    assert api.links() == []


def patch_client(monkeypatch, client):  # noqa: F811
    real = api_client.ApiClient
    monkeypatch.setattr(api_client, "ApiClient", lambda: real(BASE, http=client))


def login(at):
    at.text_input[0].set_value("test-biz-a")
    at.text_input[1].set_value("a@test.local")
    at.text_input[2].set_value(TEST_PASSWORD)
    at.button[0].click()
    return at.run()


def test_screen_login_and_draft_listing(client, shop, monkeypatch):  # noqa: F811
    patch_client(monkeypatch, client)
    make_draft(client, shop, "٣ سكر\n٥ بسكويت")
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert not at.exception and at.title[0].value.startswith("وسيط AI")
    at = login(at)
    assert not at.exception, at.exception
    assert any("استثناءات مفتوحة: 1" in e.label for e in at.expander)
    # submit is disabled while an exception is open
    submit = [b for b in at.button if b.label == "تقديم للاعتماد"][0]
    assert submit.disabled


def test_screen_wrong_password_shows_error(client, shop, monkeypatch):  # noqa: F811
    patch_client(monkeypatch, client)
    at = AppTest.from_file(APP, default_timeout=30).run()
    at.text_input[0].set_value("test-biz-a")
    at.text_input[1].set_value("a@test.local")
    at.text_input[2].set_value("nope")
    at.button[0].click()
    at.run()
    assert at.error and "me" not in at.session_state


def test_screen_dismiss_then_submit_then_confirm(client, shop, monkeypatch):  # noqa: F811
    patch_client(monkeypatch, client)
    order = make_draft(client, shop, "٣ سكر\n٥ بسكويت")
    at = login(AppTest.from_file(APP, default_timeout=30).run())
    [b for b in at.button if b.label == "تجاهل السطر"][0].click()
    at.run()
    assert not at.exception, at.exception
    submit = [b for b in at.button if b.label == "تقديم للاعتماد"][0]
    assert not submit.disabled
    submit.click()
    at.run()
    at.sidebar.radio[0].set_value("في انتظار الاعتماد").run()
    assert not at.exception, at.exception
    [b for b in at.button if b.label == "اعتماد"][0].click()
    at.run()
    assert not at.exception, at.exception
    h = shop["h"]
    assert client.get(f"/api/v1/orders/{order['id']}", headers=h).json()["status"] == "confirmed"
    stock = client.get(f"/api/v1/products/{shop['p']['SU']['id']}", headers=h).json()
    assert stock["stock_quantity"] == 97


def test_screen_resolve_with_candidate(client, shop, monkeypatch):  # noqa: F811
    patch_client(monkeypatch, client)
    order = make_draft(client, shop, "٣ رز")
    at = login(AppTest.from_file(APP, default_timeout=30).run())
    sel = [s for s in at.selectbox if s.label == "المنتج"][0]
    sel.select_index(0)
    at.run()
    [b for b in at.button if b.label == "حسم بالمنتج ده"][0].click()
    at.run()
    assert not at.exception, at.exception
    o = client.get(f"/api/v1/orders/{order['id']}", headers=shop["h"]).json()
    assert len(o["items"]) == 1 and o["items"][0]["quantity"] == 3
    assert o["exceptions"][0]["status"] == "resolved"


def test_screen_telegram_code(client, shop, monkeypatch):  # noqa: F811
    patch_client(monkeypatch, client)
    at = login(AppTest.from_file(APP, default_timeout=30).run())
    at.sidebar.radio[0].set_value("تيليجرام").run()
    at.selectbox[0].select_index(0).run()
    [b for b in at.button if b.label == "إنشاء كود"][0].click()
    at.run()
    assert not at.exception and "/start " in at.success[0].value
