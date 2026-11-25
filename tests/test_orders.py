"""Order lifecycle, price resolution, stock deduction, audit, isolation."""

from decimal import Decimal

from tests.helpers import auth_headers


def H(biz):
    return auth_headers(biz)


def setup_order(client, ids, qty=2):
    biz = ids["a"]
    c = client.post("/api/v1/customers", headers=H(biz), json={"name": "عميل"}).json()
    r = client.post("/api/v1/orders", headers=H(biz), json={
        "customer_id": c["id"], "channel": "whatsapp", "source_text": "عايز اتنين من أ",
        "items": [{"product_id": str(ids["product_a"]), "quantity": qty}],
    })
    assert r.status_code == 201, r.text
    return c, r.json()


def test_create_order_uses_catalog_price(client, two_businesses):
    _, o = setup_order(client, two_businesses)
    assert o["status"] == "draft" and o["channel"] == "whatsapp"
    assert Decimal(o["total"]) == Decimal("20.00")
    assert o["items"][0]["price_source"] == "catalog"


def test_customer_price_overrides_catalog(client, two_businesses):
    ids = two_businesses
    biz = ids["a"]
    c = client.post("/api/v1/customers", headers=H(biz), json={"name": "عميل VIP"}).json()
    client.put(f"/api/v1/customers/{c['id']}/prices/{ids['product_a']}", headers=H(biz),
               json={"price": "7.50"})
    o = client.post("/api/v1/orders", headers=H(biz), json={
        "customer_id": c["id"], "items": [{"product_id": str(ids["product_a"]), "quantity": 4}],
    }).json()
    assert o["items"][0]["price_source"] == "customer"
    assert Decimal(o["items"][0]["unit_price"]) == Decimal("7.50")
    assert Decimal(o["total"]) == Decimal("30.00")


def test_price_frozen_after_catalog_change(client, two_businesses):
    ids = two_businesses
    _, o = setup_order(client, ids)
    client.patch(f"/api/v1/products/{ids['product_a']}", headers=H(ids["a"]),
                 json={"price": "99.00"})
    again = client.get(f"/api/v1/orders/{o['id']}", headers=H(ids["a"])).json()
    assert Decimal(again["total"]) == Decimal("20.00")


def test_full_lifecycle_confirm_deducts_stock(client, two_businesses):
    ids = two_businesses
    biz, pid = ids["a"], ids["product_a"]  # stock 5
    _, o = setup_order(client, ids, qty=3)
    r = client.post(f"/api/v1/orders/{o['id']}/submit", headers=H(biz))
    assert r.json()["status"] == "pending"
    # stock untouched until confirmation
    assert client.get(f"/api/v1/products/{pid}", headers=H(biz)).json()["stock_quantity"] == 5
    r = client.post(f"/api/v1/orders/{o['id']}/confirm", headers=H(biz))
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    assert r.json()["decided_at"] is not None
    assert client.get(f"/api/v1/products/{pid}", headers=H(biz)).json()["stock_quantity"] == 2
    moves = client.get(f"/api/v1/products/{pid}/movements", headers=H(biz)).json()
    assert moves[-1]["reason"] == "order_confirmed" and moves[-1]["order_id"] == o["id"]


def test_confirm_with_insufficient_stock_changes_nothing(client, two_businesses):
    ids = two_businesses
    biz, pid = ids["a"], ids["product_a"]  # stock 5
    _, o = setup_order(client, ids, qty=6)
    client.post(f"/api/v1/orders/{o['id']}/submit", headers=H(biz))
    r = client.post(f"/api/v1/orders/{o['id']}/confirm", headers=H(biz))
    assert r.status_code == 409
    assert client.get(f"/api/v1/orders/{o['id']}", headers=H(biz)).json()["status"] == "pending"
    assert client.get(f"/api/v1/products/{pid}", headers=H(biz)).json()["stock_quantity"] == 5


def test_reject_keeps_stock_and_stores_reason(client, two_businesses):
    ids = two_businesses
    biz, pid = ids["a"], ids["product_a"]
    _, o = setup_order(client, ids)
    client.post(f"/api/v1/orders/{o['id']}/submit", headers=H(biz))
    r = client.post(f"/api/v1/orders/{o['id']}/reject", headers=H(biz), json={"reason": "سعر"})
    assert r.json()["status"] == "rejected" and r.json()["rejection_reason"] == "سعر"
    assert client.get(f"/api/v1/products/{pid}", headers=H(biz)).json()["stock_quantity"] == 5


def test_invalid_transitions_409(client, two_businesses):
    ids = two_businesses
    biz = ids["a"]
    _, o = setup_order(client, ids)
    oid = o["id"]
    assert client.post(f"/api/v1/orders/{oid}/confirm", headers=H(biz)).status_code == 409  # draft
    assert client.post(f"/api/v1/orders/{oid}/reject", headers=H(biz)).status_code == 409
    client.post(f"/api/v1/orders/{oid}/submit", headers=H(biz))
    assert client.post(f"/api/v1/orders/{oid}/submit", headers=H(biz)).status_code == 409
    client.post(f"/api/v1/orders/{oid}/confirm", headers=H(biz))
    assert client.post(f"/api/v1/orders/{oid}/confirm", headers=H(biz)).status_code == 409
    assert client.post(f"/api/v1/orders/{oid}/reject", headers=H(biz)).status_code == 409


def test_cannot_edit_after_submit_or_submit_empty(client, two_businesses):
    ids = two_businesses
    biz = ids["a"]
    c, o = setup_order(client, ids)
    client.post(f"/api/v1/orders/{o['id']}/submit", headers=H(biz))
    r = client.post(f"/api/v1/orders/{o['id']}/items", headers=H(biz),
                    json={"product_id": str(ids["product_a"]), "quantity": 1})
    assert r.status_code == 409
    empty = client.post("/api/v1/orders", headers=H(biz), json={"customer_id": c["id"]}).json()
    assert client.post(f"/api/v1/orders/{empty['id']}/submit", headers=H(biz)).status_code == 409


def test_add_remove_item_and_duplicate(client, two_businesses):
    ids = two_businesses
    biz = ids["a"]
    c = client.post("/api/v1/customers", headers=H(biz), json={"name": "ع"}).json()
    o = client.post("/api/v1/orders", headers=H(biz), json={"customer_id": c["id"]}).json()
    body = {"product_id": str(ids["product_a"]), "quantity": 2}
    r = client.post(f"/api/v1/orders/{o['id']}/items", headers=H(biz), json=body)
    assert r.status_code == 201 and Decimal(r.json()["total"]) == Decimal("20.00")
    dup = client.post(f"/api/v1/orders/{o['id']}/items", headers=H(biz), json=body)
    assert dup.status_code == 409
    item_id = r.json()["items"][0]["id"]
    r = client.delete(f"/api/v1/orders/{o['id']}/items/{item_id}", headers=H(biz))
    assert r.status_code == 200 and Decimal(r.json()["total"]) == 0 and r.json()["items"] == []


def test_zero_quantity_rejected(client, two_businesses):
    ids = two_businesses
    c = client.post("/api/v1/customers", headers=H(ids["a"]), json={"name": "ع"}).json()
    r = client.post("/api/v1/orders", headers=H(ids["a"]), json={
        "customer_id": c["id"], "items": [{"product_id": str(ids["product_a"]), "quantity": 0}]})
    assert r.status_code == 422


def test_inactive_product_cannot_be_ordered(client, two_businesses):
    ids = two_businesses
    biz = ids["a"]
    client.patch(f"/api/v1/products/{ids['product_a']}", headers=H(biz), json={"is_active": False})
    c = client.post("/api/v1/customers", headers=H(biz), json={"name": "ع"}).json()
    r = client.post("/api/v1/orders", headers=H(biz), json={
        "customer_id": c["id"], "items": [{"product_id": str(ids["product_a"]), "quantity": 1}]})
    assert r.status_code == 404


def test_delete_only_drafts(client, two_businesses):
    ids = two_businesses
    biz = ids["a"]
    _, o = setup_order(client, ids)
    assert client.delete(f"/api/v1/orders/{o['id']}", headers=H(biz)).status_code == 204
    assert client.get(f"/api/v1/orders/{o['id']}", headers=H(biz)).status_code == 404
    _, o2 = setup_order(client, ids)
    client.post(f"/api/v1/orders/{o2['id']}/submit", headers=H(biz))
    assert client.delete(f"/api/v1/orders/{o2['id']}", headers=H(biz)).status_code == 409


def test_list_filter_by_status(client, two_businesses):
    ids = two_businesses
    biz = ids["a"]
    _, o = setup_order(client, ids)
    setup_order(client, ids)
    client.post(f"/api/v1/orders/{o['id']}/submit", headers=H(biz))
    assert len(client.get("/api/v1/orders", headers=H(biz)).json()) == 2
    pend = client.get("/api/v1/orders?status=pending", headers=H(biz)).json()
    assert [x["id"] for x in pend] == [o["id"]]


def test_order_audit_trail(client, two_businesses, db_session):
    from app.models import AuditEvent

    ids = two_businesses
    biz = ids["a"]
    _, o = setup_order(client, ids)
    for step in ("submit", "confirm"):
        client.post(f"/api/v1/orders/{o['id']}/{step}", headers=H(biz))
    events = db_session.query(AuditEvent).filter_by(business_id=biz, entity="order").all()
    statuses = [e.payload.get("status") for e in events if e.action == "update"]
    assert statuses == ["pending", "confirmed"]
    assert db_session.query(AuditEvent).filter_by(business_id=biz, entity="stock").count() == 0


# ---- tenant isolation ----

def test_cross_tenant_order_access_404(client, two_businesses):
    ids = two_businesses
    _, o = setup_order(client, ids)
    hb = H(ids["b"])
    oid = o["id"]
    assert client.get(f"/api/v1/orders/{oid}", headers=hb).status_code == 404
    for step in ("submit", "confirm", "reject"):
        assert client.post(f"/api/v1/orders/{oid}/{step}", headers=hb).status_code == 404
    assert client.post(f"/api/v1/orders/{oid}/items", headers=hb,
                       json={"product_id": str(ids["product_a"]), "quantity": 1}).status_code == 404
    assert client.delete(f"/api/v1/orders/{oid}", headers=hb).status_code == 404
    assert client.get("/api/v1/orders", headers=hb).json() == []


def test_order_with_other_tenants_customer_or_product_404(client, two_businesses):
    ids = two_businesses
    cust_a = client.post("/api/v1/customers", headers=H(ids["a"]), json={"name": "ع"}).json()
    # B uses A's customer
    r = client.post("/api/v1/orders", headers=H(ids["b"]), json={"customer_id": cust_a["id"]})
    assert r.status_code == 404
    # B's customer with A's product
    cust_b = client.post("/api/v1/customers", headers=H(ids["b"]), json={"name": "ع"}).json()
    r = client.post("/api/v1/orders", headers=H(ids["b"]), json={
        "customer_id": cust_b["id"],
        "items": [{"product_id": str(ids["product_a"]), "quantity": 1}]})
    assert r.status_code == 404


def test_db_composite_fk_blocks_cross_tenant_order_rows(db_session, two_businesses):
    import pytest
    from sqlalchemy.exc import IntegrityError

    from app.models_orders import Customer, Order, OrderItem

    ids = two_businesses
    cust_a = Customer(business_id=ids["a"], name="ع")
    db_session.add(cust_a)
    db_session.flush()
    db_session.add(Order(business_id=ids["b"], customer_id=cust_a.id))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()

    cust_b = Customer(business_id=ids["b"], name="ع")
    db_session.add(cust_b)
    db_session.flush()
    order_b = Order(business_id=ids["b"], customer_id=cust_b.id)
    db_session.add(order_b)
    db_session.flush()
    db_session.add(OrderItem(
        business_id=ids["b"], order_id=order_b.id, product_id=ids["product_a"],
        quantity=1, unit_price=Decimal("1"), line_total=Decimal("1")))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
