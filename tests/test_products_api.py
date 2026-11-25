def test_create_product_and_audit(client, two_businesses, db_session):
    from app.models import AuditEvent

    headers = {"X-Business-ID": str(two_businesses["a"])}
    resp = client.post(
        "/api/v1/products",
        headers=headers,
        json={"sku": "A-002", "name": "أرز 5 كيلو", "unit": "bag", "price": "185.00",
              "stock_quantity": 50},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sku"] == "A-002"
    assert str(body["business_id"]) == str(two_businesses["a"])

    event = (
        db_session.query(AuditEvent)
        .filter_by(entity="product", entity_id=body["id"], action="create")
        .one()
    )
    assert event.payload["sku"] == "A-002"


def test_update_and_delete_write_audit(client, two_businesses, db_session):
    from app.models import AuditEvent

    headers = {"X-Business-ID": str(two_businesses["a"])}
    pid = str(two_businesses["product_a"])
    resp = client.patch(f"/api/v1/products/{pid}", headers=headers, json={"price": "12.50"})
    assert resp.status_code == 200
    assert db_session.query(AuditEvent).filter_by(
        entity="product", entity_id=pid, action="update"
    ).count() == 1

    resp = client.delete(f"/api/v1/products/{pid}", headers=headers)
    assert resp.status_code == 204
    assert db_session.query(AuditEvent).filter_by(
        entity="product", entity_id=pid, action="delete"
    ).count() == 1


def test_alias_create_and_conflict(client, two_businesses):
    headers = {"X-Business-ID": str(two_businesses["a"])}
    pid = str(two_businesses["product_a"])
    resp = client.post(f"/api/v1/products/{pid}/aliases", headers=headers, json={"alias": "رز"})
    assert resp.status_code == 201
    dup = client.post(f"/api/v1/products/{pid}/aliases", headers=headers, json={"alias": "رز"})
    assert dup.status_code == 409


def test_missing_business_header_and_unknown_business(client, two_businesses):
    assert client.get("/api/v1/products").status_code == 422
    resp = client.get(
        "/api/v1/products", headers={"X-Business-ID": str(two_businesses["nonexistent"])}
    )
    assert resp.status_code == 404
