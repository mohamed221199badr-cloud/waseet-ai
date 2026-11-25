"""Text -> draft order endpoint, exceptions, product deactivation, M3 migration."""

from decimal import Decimal

import pytest
from sqlalchemy import create_engine, inspect

from tests.helpers import auth_headers
from tests.test_migrations import _alembic, scratch_url  # noqa: F401

INTAKE = "/api/v1/intake/text-order"


@pytest.fixture()
def shop(client, db_session, two_businesses):
    """Business A with a small catalog, aliases and one customer."""
    ids = two_businesses
    h = auth_headers(ids["a"])
    products = {}
    for sku_, name, price, stock, aliases in [
        ("SU", "سكر 1 كيلو", "28.00", 100, ["سكر"]),
        ("SA", "ملح ناعم 1 كيلو", "10.00", 50, ["ملح"]),
        ("R1", "أرز مصري 1 كيلو", "40.00", 20, ["رز 1 كيلو"]),
        ("R5", "أرز مصري 5 كيلو", "185.00", 20, ["رز 5 كيلو"]),
    ]:
        p = client.post("/api/v1/products", headers=h, json={
            "sku": sku_, "name": name, "price": price, "stock_quantity": stock}).json()
        products[sku_] = p
        for a in aliases:
            client.post(f"/api/v1/products/{p['id']}/aliases", headers=h, json={"alias": a})
    cust = client.post("/api/v1/customers", headers=h,
                       json={"name": "عميل", "phone": "01000000001"}).json()
    return {"h": h, "p": products, "c": cust, "ids": ids}


def test_text_becomes_draft_with_items_and_exceptions(client, shop):
    r = client.post(INTAKE, headers=shop["h"], json={
        "text": "٣ سكر\n٢ ملح\n٥ بسكويت\n٣ رز", "customer_id": shop["c"]["id"],
        "channel": "whatsapp"})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["matched_count"] == 2 and body["exception_count"] == 2
    order = body["order"]
    assert order["status"] == "draft" and order["channel"] == "whatsapp"
    assert order["source_text"].startswith("٣ سكر")
    assert {i["quantity"] for i in order["items"]} == {3, 2}
    assert Decimal(order["total"]) == Decimal("3") * Decimal("28.00") + 2 * Decimal("10.00")
    reasons = {e["raw_text"]: e["reason"] for e in order["exceptions"]}
    assert reasons["5 بسكويت"] == "not_found"
    assert reasons["3 رز"] == "ambiguous"
    ambiguous = next(e for e in order["exceptions"] if e["reason"] == "ambiguous")
    assert {c["sku"] for c in ambiguous["candidates"]} == {"R1", "R5"}


def test_customer_price_is_used_and_stock_is_untouched(client, shop):
    h, c, p = shop["h"], shop["c"], shop["p"]
    client.put(f"/api/v1/customers/{c['id']}/prices/{p['SU']['id']}", headers=h,
               json={"price": "25.00"})
    r = client.post(INTAKE, headers=h, json={"text": "4 سكر", "customer_id": c["id"]}).json()
    item = r["order"]["items"][0]
    assert item["price_source"] == "customer" and Decimal(item["unit_price"]) == Decimal("25.00")
    stock = client.get(f"/api/v1/products/{p['SU']['id']}", headers=h).json()["stock_quantity"]
    assert stock == 100  # intake never deducts stock; only confirm does


def test_customer_by_phone_and_exactly_one_selector(client, shop):
    h = shop["h"]
    ok = client.post(INTAKE, headers=h, json={"text": "2 سكر", "customer_phone": "01000000001"})
    assert ok.status_code == 201
    assert ok.json()["order"]["customer_id"] == shop["c"]["id"]
    both = client.post(INTAKE, headers=h, json={
        "text": "2 سكر", "customer_phone": "01000000001", "customer_id": shop["c"]["id"]})
    neither = client.post(INTAKE, headers=h, json={"text": "2 سكر"})
    assert both.status_code == neither.status_code == 422
    unknown = client.post(INTAKE, headers=h, json={"text": "2 سكر", "customer_phone": "0199"})
    assert unknown.status_code == 404


def test_requires_login_and_is_tenant_isolated(client, shop):
    body = {"text": "2 سكر", "customer_id": shop["c"]["id"]}
    assert client.post(INTAKE, json=body).status_code == 401
    # business B cannot use business A's customer, nor see A's products in matching
    hb = auth_headers(shop["ids"]["b"])
    assert client.post(INTAKE, headers=hb, json=body).status_code == 404
    cb = client.post("/api/v1/customers", headers=hb, json={"name": "عميل ب"}).json()
    r = client.post(INTAKE, headers=hb, json={"text": "2 سكر", "customer_id": cb["id"]}).json()
    assert r["matched_count"] == 0 and r["order"]["items"] == []
    assert r["order"]["exceptions"][0]["reason"] == "not_found"


def test_no_lines_is_422_and_creates_nothing(client, shop):
    r = client.post(INTAKE, headers=shop["h"], json={"text": " ,, \n ", "customer_id":
                                                    shop["c"]["id"]})
    assert r.status_code == 422
    assert client.get("/api/v1/orders", headers=shop["h"]).json() == []


def test_submit_blocked_until_exceptions_resolved_or_dismissed(client, shop):
    h, p = shop["h"], shop["p"]
    d = client.post(INTAKE, headers=h, json={
        "text": "3 سكر\n2 رز\n4 بسكويت\nملح", "customer_id": shop["c"]["id"]}).json()["order"]
    oid = d["id"]
    assert client.post(f"/api/v1/orders/{oid}/submit", headers=h).status_code == 409
    by_raw = {e["raw_text"]: e for e in d["exceptions"]}
    # owner picks the 5kg rice for the ambiguous line
    r = client.post(f"/api/v1/orders/{oid}/exceptions/{by_raw['2 رز']['id']}/resolve",
                    headers=h, json={"product_id": p["R5"]["id"]})
    assert r.status_code == 200, r.text
    assert any(i["product_id"] == p["R5"]["id"] and i["quantity"] == 2
               for i in r.json()["items"])
    # the salt line had no quantity: resolving without one is refused, with one works
    salt = by_raw["ملح"]
    url = f"/api/v1/orders/{oid}/exceptions/{salt['id']}/resolve"
    assert salt["quantity"] is None
    assert client.post(url, headers=h, json={"product_id": p["SA"]["id"]}).status_code == 422
    assert client.post(url, headers=h, json={"product_id": p["SA"]["id"],
                                              "quantity": 6}).status_code == 200
    client.post(f"/api/v1/orders/{oid}/exceptions/{by_raw['4 بسكويت']['id']}/dismiss",
                headers=h)
    # closed exceptions cannot be closed twice
    assert client.post(f"/api/v1/orders/{oid}/exceptions/{by_raw['4 بسكويت']['id']}/dismiss",
                       headers=h).status_code == 409
    sub = client.post(f"/api/v1/orders/{oid}/submit", headers=h)
    assert sub.status_code == 200 and sub.json()["status"] == "pending"
    conf = client.post(f"/api/v1/orders/{oid}/confirm", headers=h)
    assert conf.status_code == 200
    stock = client.get(f"/api/v1/products/{p['SU']['id']}", headers=h).json()["stock_quantity"]
    assert stock == 97


def test_cross_tenant_exception_resolution_is_404(client, shop):
    h = shop["h"]
    d = client.post(INTAKE, headers=h, json={"text": "3 بسكويت",
                                             "customer_id": shop["c"]["id"]}).json()["order"]
    hb = auth_headers(shop["ids"]["b"])
    url = f"/api/v1/orders/{d['id']}/exceptions/{d['exceptions'][0]['id']}/dismiss"
    assert client.post(url, headers=hb).status_code == 404


def test_inactive_products_are_not_matched(client, shop):
    h, p = shop["h"], shop["p"]
    client.patch(f"/api/v1/products/{p['SU']['id']}", headers=h, json={"is_active": False})
    r = client.post(INTAKE, headers=h, json={"text": "3 سكر",
                                             "customer_id": shop["c"]["id"]}).json()
    assert r["matched_count"] == 0


def test_delete_product_with_order_history_deactivates(client, shop):
    """The M1/M2 known issue: DELETE used to fail with a foreign key error."""
    h, p = shop["h"], shop["p"]
    d = client.post(INTAKE, headers=h, json={"text": "3 سكر",
                                             "customer_id": shop["c"]["id"]}).json()["order"]
    resp = client.delete(f"/api/v1/products/{p['SU']['id']}", headers=h)
    assert resp.status_code == 200 and resp.json()["is_active"] is False
    again = client.delete(f"/api/v1/products/{p['SU']['id']}", headers=h)
    assert again.status_code == 200  # idempotent
    # history intact, product still readable, hidden from the active list
    assert client.get(f"/api/v1/orders/{d['id']}", headers=h).json()["items"]
    assert client.get(f"/api/v1/products/{p['SU']['id']}", headers=h).status_code == 200
    active = {x["sku"] for x in client.get("/api/v1/products?is_active=true", headers=h).json()}
    assert "SU" not in active and "SA" in active
    # can be re-activated
    back = client.patch(f"/api/v1/products/{p['SU']['id']}", headers=h, json={"is_active": True})
    assert back.json()["is_active"] is True


def test_m3_migration_step_down_and_up(scratch_url):  # noqa: F811
    assert _alembic(scratch_url, "upgrade", "head").returncode == 0
    eng = create_engine(scratch_url)
    assert "order_exceptions" in inspect(eng).get_table_names()
    assert "password_hash" in {c["name"] for c in inspect(eng).get_columns("users")}
    down = _alembic(scratch_url, "downgrade", "m2_0001")
    assert down.returncode == 0, down.stderr
    insp = inspect(eng)
    assert "order_exceptions" not in insp.get_table_names()
    assert "password_hash" not in {c["name"] for c in insp.get_columns("users")}
    assert _alembic(scratch_url, "upgrade", "head").returncode == 0
    eng.dispose()
