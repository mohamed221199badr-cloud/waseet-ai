"""Customers, customer-specific prices, tenant isolation for both."""

from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError


def H(biz):
    return {"X-Business-ID": str(biz)}


def make_customer(client, biz, name="تاجر التجزئة", phone="01000000001"):
    r = client.post("/api/v1/customers", headers=H(biz), json={"name": name, "phone": phone})
    assert r.status_code == 201, r.text
    return r.json()


def test_customer_crud_and_audit(client, two_businesses, db_session):
    from app.models import AuditEvent

    biz = two_businesses["a"]
    c = make_customer(client, biz)
    assert client.get(f"/api/v1/customers/{c['id']}", headers=H(biz)).json()["name"] == c["name"]
    r = client.patch(f"/api/v1/customers/{c['id']}", headers=H(biz), json={"address": "القاهرة"})
    assert r.status_code == 200 and r.json()["address"] == "القاهرة"
    actions = [
        e.action for e in db_session.query(AuditEvent).filter_by(entity="customer", business_id=biz)
    ]
    assert actions == ["create", "update"]


def test_duplicate_phone_same_business_409_but_ok_across(client, two_businesses):
    make_customer(client, two_businesses["a"])
    r = client.post("/api/v1/customers", headers=H(two_businesses["a"]),
                    json={"name": "مكرر", "phone": "01000000001"})
    assert r.status_code == 409
    r = client.post("/api/v1/customers", headers=H(two_businesses["b"]),
                    json={"name": "نفس الرقم شركة تانية", "phone": "01000000001"})
    assert r.status_code == 201


def test_cross_tenant_customer_404(client, two_businesses):
    c = make_customer(client, two_businesses["a"])
    hb = H(two_businesses["b"])
    assert client.get(f"/api/v1/customers/{c['id']}", headers=hb).status_code == 404
    assert client.patch(f"/api/v1/customers/{c['id']}", headers=hb,
                        json={"name": "x"}).status_code == 404
    assert client.get(f"/api/v1/customers/{c['id']}/prices", headers=hb).status_code == 404
    assert client.get("/api/v1/customers", headers=hb).json() == []


def test_set_update_delete_customer_price(client, two_businesses):
    biz = two_businesses["a"]
    c = make_customer(client, biz)
    url = f"/api/v1/customers/{c['id']}/prices/{two_businesses['product_a']}"
    assert client.put(url, headers=H(biz), json={"price": "8.50"}).status_code == 200
    r = client.put(url, headers=H(biz), json={"price": "7.25"})
    assert Decimal(r.json()["price"]) == Decimal("7.25")
    prices = client.get(f"/api/v1/customers/{c['id']}/prices", headers=H(biz)).json()
    assert len(prices) == 1  # upsert, not duplicate
    assert client.delete(url, headers=H(biz)).status_code == 204
    assert client.delete(url, headers=H(biz)).status_code == 404


def test_price_for_other_tenants_product_404(client, two_businesses):
    # tenant B's customer, tenant A's product
    c = make_customer(client, two_businesses["b"])
    url = f"/api/v1/customers/{c['id']}/prices/{two_businesses['product_a']}"
    assert client.put(url, headers=H(two_businesses["b"]), json={"price": "1"}).status_code == 404


def test_negative_price_rejected(client, two_businesses):
    biz = two_businesses["a"]
    c = make_customer(client, biz)
    url = f"/api/v1/customers/{c['id']}/prices/{two_businesses['product_a']}"
    assert client.put(url, headers=H(biz), json={"price": "-1"}).status_code == 422


def test_db_composite_fk_blocks_cross_tenant_price(db_session, two_businesses):
    from app.models_orders import Customer, CustomerPrice

    cust_b = Customer(business_id=two_businesses["b"], name="عميل ب")
    db_session.add(cust_b)
    db_session.flush()
    db_session.add(CustomerPrice(
        business_id=two_businesses["b"], customer_id=cust_b.id,
        product_id=two_businesses["product_a"], price=Decimal("1.00"),
    ))
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
