"""Dashboard v2 API: bot orders visible, role-limited views, summary, decide, tenancy."""

import pytest

from app.auth import create_access_token
from app.chat import callbacks as cb_codec
from app.models import User
from app.telegram import service as svc
from tests.helpers import auth_headers
from tests.test_checkout import _fill_cart
from tests.test_intake import shop  # noqa: F401
from tests.test_menu_buttons_v18 import CHAT, _cb, linked, menu, tap  # noqa: F401
from tests.test_migrations import _alembic, scratch_url  # noqa: F401

B = "/api/v1/board"


def _order(db, menu):  # noqa: F811
    _fill_cart(db, menu)
    tap(db, cb_codec.build("ckgo"))
    tap(db, cb_codec.build("ckdel", "pickup"))
    svc.handle_update(db, _cb(CHAT, cb_codec.build("ckok")))


def _hdr(user):
    tok, _ = create_access_token(user.id, user.business_id)
    return {"Authorization": f"Bearer {tok}"}


def _staff(db, linked, role):  # noqa: F811
    from tests.conftest import test_password_hash
    u = User(business_id=linked["ids"]["a"], full_name=role, email=f"{role}@t.local",
             role=role, password_hash=test_password_hash())
    db.add(u)
    db.commit()
    return u


def test_owner_sees_bot_order_with_lines_and_money(client, db_session, linked, menu):  # noqa: F811
    _order(db_session, menu)
    r = client.get(f"{B}/orders", headers=auth_headers(linked["ids"]["a"]))
    assert r.status_code == 200 and len(r.json()) == 1
    o = r.json()[0]
    assert o["status"] == "confirmed" and o["method"] == "pickup"
    assert o["lines"][0]["item"] and o["lines"][0]["unit_price"] and o["total"]
    assert "E" not in o["total"] and len(o["short_id"]) == 8


def test_kitchen_sees_items_without_money_or_phone(client, db_session, linked, menu):  # noqa: F811
    _order(db_session, menu)
    k = _staff(db_session, linked, "kitchen")
    o = client.get(f"{B}/orders", headers=_hdr(k)).json()[0]
    assert o["total"] is None and o["customer_phone"] is None
    assert o["lines"][0]["unit_price"] is None and o["lines"][0]["line_total"] is None
    assert client.get(f"{B}/summary", headers=_hdr(k)).status_code == 403
    assert client.get(f"{B}/customers", headers=_hdr(k)).status_code == 403


def test_summary_counts_confirmed_orders(client, db_session, linked, menu):  # noqa: F811
    _order(db_session, menu)
    s = client.get(f"{B}/summary", headers=auth_headers(linked["ids"]["a"])).json()
    assert s["orders"] == 1 and s["by_method"]["pickup"]["orders"] == 1
    assert s["top_items"] and float(s["revenue"]) > 0 and s["pending"] == 0
    y = client.get(f"{B}/summary?day=yesterday", headers=auth_headers(linked["ids"]["a"])).json()
    assert y["orders"] == 0


def test_other_business_sees_nothing(client, db_session, linked, menu):  # noqa: F811
    _order(db_session, menu)
    assert client.get(f"{B}/orders?day=all",
                      headers=auth_headers(linked["ids"]["b"])).json() == []


def test_decide_pending_only_and_audited(client, db_session, linked, menu, monkeypatch):  # noqa: F811
    from sqlalchemy import select

    from app.config import settings
    from app.models import AuditEvent
    from app.models_orders import Order
    monkeypatch.setattr(settings, "order_auto_confirm", False)
    _order(db_session, menu)
    oid = db_session.scalar(select(Order.id))
    h = auth_headers(linked["ids"]["a"])
    assert client.get(f"{B}/orders?status=pending", headers=h).json()[0]["id"] == str(oid)
    k = _staff(db_session, linked, "kitchen")
    assert client.post(f"{B}/orders/{oid}/confirm", headers=_hdr(k)).status_code == 403
    r = client.post(f"{B}/orders/{oid}/reject", headers=h, json={"reason": "خلص"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert client.post(f"{B}/orders/{oid}/confirm", headers=h).status_code == 409
    assert db_session.scalar(select(AuditEvent).where(AuditEvent.action == "reject")) is not None


def test_customers_list_has_order_stats(client, db_session, linked, menu):  # noqa: F811
    _order(db_session, menu)
    rows = client.get(f"{B}/customers", headers=auth_headers(linked["ids"]["a"])).json()
    assert any(r["orders"] == 1 and float(r["spent"]) > 0 and r["last_order"] for r in rows)


def test_telegram_links_expose_owner_flag(client, db_session, linked, menu):  # noqa: F811
    rows = client.get("/api/v1/telegram/links", headers=auth_headers(linked["ids"]["a"])).json()
    assert rows and "is_owner" in rows[0]


@pytest.mark.parametrize("path", ["/orders", "/summary", "/customers"])
def test_board_requires_login(client, path):
    assert client.get(B + path).status_code == 401
