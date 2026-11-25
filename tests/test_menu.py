"""Menu API (Phase 1, 1.4): CRUD rules, one default variant, weight unit_label,
availability and price permissions, tenancy."""

import uuid

from tests.helpers import auth_headers

MENU = "/api/v1/menu"


def _item(client, h, **over):
    body = {
        "category": "اللحوم", "name": "مفروم احمر", "sold_by": "weight",
        "unit_label": "كيلو", "variants": [{"name": "سعر 1", "price": "450.00"}],
        "aliases": ["مفروم"],
    }
    body.update(over)
    return client.post(f"{MENU}/items", headers=h, json=body)


def test_create_item_defaults_and_alias(client, two_businesses):
    h = auth_headers(two_businesses["a"])
    r = _item(client, h, variants=[
        {"name": "سعر 2", "price": "450.00", "sort_order": 2},
        {"name": "سعر 1", "price": "430.00", "sort_order": 1},
    ])
    assert r.status_code == 201, r.text
    item = r.json()
    # no variant marked default: the first by sort_order wins
    assert [v["name"] for v in item["variants"]] == ["سعر 1", "سعر 2"]
    assert item["variants"][0]["is_default"] is True
    assert item["aliases"][0]["alias"] == "مفروم"
    assert client.get(f"{MENU}/categories", headers=h).json()[0]["name"] == "اللحوم"


def test_two_marked_defaults_rejected_and_weight_needs_label(client, two_businesses):
    h = auth_headers(two_businesses["a"])
    r = _item(client, h, variants=[
        {"name": "أ", "is_default": True}, {"name": "ب", "is_default": True}])
    assert r.status_code == 409
    assert _item(client, h, unit_label=None).status_code == 422
    # NULL price is allowed: it means "ask the cashier" (B4)
    r = _item(client, h, name="كبيبة شامي", variants=[{"name": "سعر 1", "price": None}])
    assert r.status_code == 201 and r.json()["variants"][0]["price"] is None


def test_same_name_lives_in_two_categories(client, two_businesses):
    h = auth_headers(two_businesses["a"])
    assert _item(client, h, name="كبدة ضاني", aliases=[]).status_code == 201
    r = _item(client, h, name="كبدة ضاني", category="الغازي والحلويات", aliases=[])
    assert r.status_code == 201
    # the same (category, name) twice in one business is a conflict
    assert _item(client, h, name="كبدة ضاني", aliases=[]).status_code == 409


def test_availability_toggle_permission(client, two_businesses, db_session):
    from app.models import User
    from tests.conftest import test_password_hash

    h = auth_headers(two_businesses["a"])
    item = _item(client, h).json()
    # a kitchen user may toggle availability but not edit the item
    db_session.add(User(business_id=two_businesses["a"], full_name="مطبخ",
                        email="kitchen@test.local", role="kitchen",
                        password_hash=test_password_hash()))
    db_session.commit()
    hk = auth_headers(two_businesses["a"])
    # auth_headers picks the first user of the business; make one for the kitchen user
    import sqlalchemy as sa

    from app.auth import create_access_token
    kitchen = db_session.scalar(sa.select(User).where(User.email == "kitchen@test.local"))
    tok, _ = create_access_token(kitchen.id, kitchen.business_id)
    hk = {"Authorization": f"Bearer {tok}"}
    assert client.patch(f"{MENU}/items/{item['id']}", headers=hk,
                        json={"name": "x"}).status_code == 403
    r = client.post(f"{MENU}/items/{item['id']}/availability", headers=hk,
                    json={"is_available": False})
    assert r.status_code == 200 and r.json()["is_available"] is False
    assert client.post(f"{MENU}/items/{item['id']}/availability", headers=h,
                       json={"is_available": True}).status_code == 200


def test_price_change_needs_price_manage(client, two_businesses, db_session):
    from app.models import User
    from tests.conftest import test_password_hash

    h = auth_headers(two_businesses["a"])
    item = _item(client, h).json()
    vid = item["variants"][0]["id"]
    # owner (all permissions) changes the price
    r = client.patch(f"{MENU}/items/{item['id']}/variants/{vid}", headers=h,
                     json={"price": "460.00"})
    assert r.status_code == 200 and r.json()["price"] == "460.00"
    # a cashier (MENU_MANAGE not held at all) is refused outright
    db_session.add(User(business_id=two_businesses["a"], full_name="كاشير",
                        email="cashier@test.local", role="cashier",
                        password_hash=test_password_hash()))
    db_session.commit()
    import sqlalchemy as sa

    from app.auth import create_access_token
    cashier = db_session.scalar(sa.select(User).where(User.email == "cashier@test.local"))
    tok, _ = create_access_token(cashier.id, cashier.business_id)
    hc = {"Authorization": f"Bearer {tok}"}
    assert client.patch(f"{MENU}/items/{item['id']}/variants/{vid}", headers=hc,
                        json={"price": "1.00"}).status_code == 403


def test_menu_is_tenant_scoped(client, two_businesses):
    ha, hb = auth_headers(two_businesses["a"]), auth_headers(two_businesses["b"])
    item = _item(client, ha).json()
    assert client.get(f"{MENU}/items/{item['id']}", headers=hb).status_code == 404
    assert client.get(f"{MENU}/items", headers=hb).json() == []
    # an alias is unique per business, not globally
    assert _item(client, hb, name="صنف ب").status_code == 201


def test_variant_default_switch_and_delete(client, two_businesses):
    h = auth_headers(two_businesses["a"])
    item = _item(client, h, variants=[
        {"name": "أ", "price": "10"}, {"name": "ب", "price": "20"}]).json()
    va, vb = item["variants"]
    r = client.post(f"{MENU}/items/{item['id']}/variants/{vb['id']}/default", headers=h)
    assert r.status_code == 200
    item = client.get(f"{MENU}/items/{item['id']}", headers=h).json()
    assert [v["is_default"] for v in item["variants"]] == [False, True]
    # deleting the default moves it to the remaining variant; the last variant stays
    assert client.delete(f"{MENU}/items/{item['id']}/variants/{vb['id']}",
                         headers=h).status_code == 204
    item = client.get(f"{MENU}/items/{item['id']}", headers=h).json()
    assert item["variants"][0]["is_default"] is True
    assert client.delete(f"{MENU}/items/{item['id']}/variants/{va['id']}",
                         headers=h).status_code == 409
    # deleting a variant of another tenant's item is 404
    assert client.delete(f"{MENU}/items/{uuid.uuid4()}/variants/{va['id']}",
                         headers=h).status_code == 404
