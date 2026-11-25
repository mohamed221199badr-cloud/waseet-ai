"""Inventory ledger, stock floor, low-stock list, tenant isolation."""

from tests.helpers import auth_headers


def H(biz):
    return auth_headers(biz)


def test_restock_and_adjust_write_ledger(client, two_businesses):
    biz, pid = two_businesses["a"], two_businesses["product_a"]  # starts at 5
    r = client.post(f"/api/v1/products/{pid}/stock", headers=H(biz),
                    json={"delta": 20, "reason": "restock", "note": "شحنة"})
    assert r.status_code == 200 and r.json()["stock_quantity"] == 25
    r = client.post(f"/api/v1/products/{pid}/stock", headers=H(biz),
                    json={"delta": -3, "reason": "adjustment"})
    assert r.json()["stock_quantity"] == 22
    moves = client.get(f"/api/v1/products/{pid}/movements", headers=H(biz)).json()
    assert [(m["delta"], m["balance_after"], m["reason"]) for m in moves] == [
        (20, 25, "restock"), (-3, 22, "adjustment")]


def test_stock_cannot_go_negative(client, two_businesses):
    biz, pid = two_businesses["a"], two_businesses["product_a"]
    r = client.post(f"/api/v1/products/{pid}/stock", headers=H(biz), json={"delta": -6})
    assert r.status_code == 409
    prod = client.get(f"/api/v1/products/{pid}", headers=H(biz)).json()
    assert prod["stock_quantity"] == 5
    assert client.get(f"/api/v1/products/{pid}/movements", headers=H(biz)).json() == []


def test_zero_delta_rejected(client, two_businesses):
    r = client.post(f"/api/v1/products/{two_businesses['product_a']}/stock",
                    headers=H(two_businesses["a"]), json={"delta": 0})
    assert r.status_code == 422


def test_cross_tenant_stock_404(client, two_businesses):
    pid, hb = two_businesses["product_a"], H(two_businesses["b"])
    assert client.post(f"/api/v1/products/{pid}/stock", headers=hb,
                       json={"delta": 5}).status_code == 404
    assert client.get(f"/api/v1/products/{pid}/movements", headers=hb).status_code == 404


def test_low_stock_list_is_tenant_scoped(client, two_businesses):
    a = client.get("/api/v1/inventory/low-stock?threshold=6", headers=H(two_businesses["a"])).json()
    assert [p["sku"] for p in a] == ["A-001"]
    b = client.get("/api/v1/inventory/low-stock?threshold=6", headers=H(two_businesses["b"])).json()
    assert b == []  # B-001 has 7
