# ruff: noqa: F811
"""Dashboard v2: the API client against the real app, and the Streamlit script run headless
with Streamlit's AppTest against that same app (no browser, no real server)."""

from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from app.chat import callbacks as cb_codec
from app.models import User
from app.telegram import service as svc
from tests.conftest import TEST_PASSWORD, test_password_hash
from tests.test_checkout import _fill_cart
from tests.test_intake import shop  # noqa: F401
from tests.test_menu_buttons_v18 import CHAT, _cb, linked, menu, tap  # noqa: F401
from tests.test_migrations import _alembic, scratch_url  # noqa: F401
from ui import api_client

APP = str(Path(__file__).resolve().parent.parent / "ui" / "app.py")
BASE = "http://testserver/api/v1"


def _order(db, menu):  # noqa: F811
    _fill_cart(db, menu)
    tap(db, cb_codec.build("ckgo"))
    tap(db, cb_codec.build("ckdel", "pickup"))
    svc.handle_update(db, _cb(CHAT, cb_codec.build("ckok")))


def patch_client(monkeypatch, client):
    real = api_client.ApiClient
    monkeypatch.setenv("WASEET_BUSINESS_SLUG", "test-biz-a")
    monkeypatch.setattr(api_client, "ApiClient", lambda: real(BASE, http=client))


def login(at, email="a@test.local"):
    at.text_input[0].set_value(email)
    at.text_input[1].set_value(TEST_PASSWORD)
    at.button[0].click()
    return at.run()


def test_client_board_calls_and_errors(client, db_session, linked, menu):  # noqa: F811
    _order(db_session, menu)
    a = api_client.ApiClient(BASE, http=client)
    with pytest.raises(api_client.ApiError) as ei:
        a.board_orders()
    assert ei.value.status == 401
    a.login("test-biz-a", "a@test.local", TEST_PASSWORD)
    assert len(a.board_orders("today")) == 1
    assert a.board_summary()["orders"] == 1
    assert a.board_customers() and a.links()


def test_owner_sees_all_pages_and_the_bot_order(  # noqa: F811
        client, db_session, linked, menu, monkeypatch):
    patch_client(monkeypatch, client)
    _order(db_session, menu)
    at = login(AppTest.from_file(APP, default_timeout=30).run())
    assert not at.exception, at.exception
    assert list(at.sidebar.radio[0].options) == [
        "الطلبات", "المطبخ", "الحسابات", "المنيو", "العملاء", "البوت", "الموظفون"]
    assert at.title[0].value == "الطلبات"
    assert any("✅ متأكد" in e.label for e in at.expander)
    for page in ["المطبخ", "الحسابات", "المنيو", "العملاء", "البوت", "الموظفون"]:
        at.sidebar.radio[0].set_value(page).run()
        assert not at.exception, (page, at.exception)
        assert at.title[0].value == page
    # legacy pages are gone
    assert "جرّب البوت" not in at.sidebar.radio[0].options
    assert "المسودات" not in at.sidebar.radio[0].options


def test_kitchen_role_sees_only_kitchen_without_prices(  # noqa: F811
        client, db_session, linked, menu, monkeypatch):
    patch_client(monkeypatch, client)
    _order(db_session, menu)
    db_session.add(User(business_id=linked["ids"]["a"], full_name="مطبخ", email="k@test.local",
                        role="kitchen", password_hash=test_password_hash()))
    db_session.commit()
    at = login(AppTest.from_file(APP, default_timeout=30).run(), "k@test.local")
    assert not at.exception, at.exception
    assert list(at.sidebar.radio[0].options) == ["المطبخ"]
    text = " ".join(m.value for m in at.markdown) + " ".join(
        str(getattr(w, "value", "")) for w in at.text)
    assert "ج" not in text.replace("جاري", "") or "×" in text  # items shown, no money
    assert not at.metric
