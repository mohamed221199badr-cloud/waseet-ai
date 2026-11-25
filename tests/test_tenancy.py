"""Cross-tenant isolation: 404 at API level, FK violation at DB level."""

import pytest
from sqlalchemy.exc import IntegrityError

from tests.helpers import auth_headers


def test_cross_tenant_get_returns_404(client, two_businesses):
    headers_b = auth_headers(two_businesses["b"])
    resp = client.get(f"/api/v1/products/{two_businesses['product_a']}", headers=headers_b)
    assert resp.status_code == 404


def test_cross_tenant_update_and_delete_return_404(client, two_businesses):
    headers_b = auth_headers(two_businesses["b"])
    pid = two_businesses["product_a"]
    assert client.patch(f"/api/v1/products/{pid}", headers=headers_b,
                        json={"price": "1.00"}).status_code == 404
    assert client.delete(f"/api/v1/products/{pid}", headers=headers_b).status_code == 404


def test_cross_tenant_alias_returns_404(client, two_businesses):
    headers_b = auth_headers(two_businesses["b"])
    resp = client.post(
        f"/api/v1/products/{two_businesses['product_a']}/aliases",
        headers=headers_b,
        json={"alias": "محاولة تسريب"},
    )
    assert resp.status_code == 404


def test_tenant_list_shows_only_own_products(client, two_businesses):
    for biz, expected in (("a", {"A-001"}), ("b", {"B-001"})):
        resp = client.get("/api/v1/products",
                          headers=auth_headers(two_businesses[biz]))
        assert resp.status_code == 200
        assert {p["sku"] for p in resp.json()} == expected


def test_db_level_composite_fk_blocks_cross_tenant_alias(db_session, two_businesses):
    """Inserting an alias for tenant B pointing at tenant A's product must fail
    at the database level, even bypassing the API entirely."""
    from app.models import ProductAlias

    db_session.add(
        ProductAlias(
            business_id=two_businesses["b"],
            product_id=two_businesses["product_a"],
            alias="leak",
        )
    )
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()


def test_duplicate_sku_same_business_rejected(client, two_businesses):
    headers = auth_headers(two_businesses["a"])
    resp = client.post(
        "/api/v1/products", headers=headers,
        json={"sku": "A-001", "name": "مكرر", "price": "1.00"},
    )
    assert resp.status_code == 409


def test_same_sku_allowed_across_businesses(client, two_businesses):
    headers = auth_headers(two_businesses["b"])
    resp = client.post(
        "/api/v1/products", headers=headers,
        json={"sku": "A-001", "name": "نفس الكود لشركة تانية", "price": "1.00"},
    )
    assert resp.status_code == 201, resp.text
