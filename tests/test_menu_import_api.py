"""Menu import + diff-report endpoints (Phase 1, 1.5/1.6 API surface)."""

from tests.helpers import auth_headers

MENU = "/api/v1/menu"

CSV = "category,item,variant,price,sold_by\nاللحوم,مفروم,سعر 1,450,weight\n"


def test_import_endpoint_dry_run_then_commit(client, two_businesses):
    h = auth_headers(two_businesses["a"])
    bad = client.post(f"{MENU}/import", headers=h, json={"csv_text": CSV})
    # weight without unit_label: dry run reports the row error, nothing written
    assert {e["column"] for e in bad.json()["errors"]} == {"unit_label"}
    good = CSV.replace("weight", "weight,كيلو").replace(
        "category,item,variant,price,sold_by", "category,item,variant,price,sold_by,unit_label")
    r = client.post(f"{MENU}/import", headers=h, json={"csv_text": good, "dry_run": True})
    assert r.json()["created"] == 1 and client.get(f"{MENU}/items", headers=h).json() == []
    r = client.post(f"{MENU}/import", headers=h, json={"csv_text": good, "dry_run": False})
    assert r.json()["created"] == 1
    # idempotent through the API too
    r = client.post(f"{MENU}/import", headers=h, json={"csv_text": good, "dry_run": False})
    assert (r.json()["created"], r.json()["unchanged"]) == (0, 1)


def test_diff_report(client, two_businesses):
    h = auth_headers(two_businesses["a"])
    rows = client.get(f"{MENU}/diff-report", headers=h).json()
    kinds = {r["item"]: r["kind"] for r in rows}
    # the fixture product exists only in products; an imported item only in the menu
    assert kinds.get("منتج أ") == "only_in_products"
    good = ("category,item,variant,price,sold_by\n"
            "اللحوم,منتج أ,سعر 1,999,unit\n")
    client.post(f"{MENU}/import", headers=h, json={"csv_text": good, "dry_run": False})
    kinds = {r["item"]: r["kind"] for r in client.get(f"{MENU}/diff-report",
                                                      headers=h).json()}
    assert kinds.get("منتج أ") == "price_differs"


def test_import_and_diff_need_menu_manage(client, two_businesses, db_session):
    from app.models import User
    from tests.conftest import test_password_hash
    db_session.add(User(business_id=two_businesses["a"], full_name="كاشير",
                        email="cash2@test.local", role="cashier",
                        password_hash=test_password_hash()))
    db_session.commit()
    import sqlalchemy as sa

    from app.auth import create_access_token
    cashier = db_session.scalar(sa.select(User).where(User.email == "cash2@test.local"))
    tok, _ = create_access_token(cashier.id, cashier.business_id)
    h = {"Authorization": f"Bearer {tok}"}
    assert client.post(f"{MENU}/import", headers=h, json={"csv_text": "x"}).status_code == 403
    assert client.get(f"{MENU}/diff-report", headers=h).status_code == 403
