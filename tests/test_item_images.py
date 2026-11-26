"""Item images (m013): per-item photo API, bulk import by filename matching."""


from sqlalchemy import select

from app.models_menu import MenuItemImage
from scripts.import_item_images import run as import_run
from tests.helpers import auth_headers

MENU = "/api/v1/menu"

PNG = bytes.fromhex("89504e470d0a1a0a") + b"x" * 100  # tiny fake png payload


def _mk_item(client, h, name="مفروم احمر", category="اللحوم"):
    r = client.post(f"{MENU}/items", headers=h, json={
        "category": category, "name": name, "sold_by": "weight",
        "unit_label": "كيلو", "variants": [{"name": "سعر 1", "price": "450.00"}]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_put_get_roundtrip_and_replace_clears_file_id(client, two_businesses, db_session):
    h = auth_headers(two_businesses["a"])
    item_id = _mk_item(client, h)
    r = client.put(f"{MENU}/items/{item_id}/image", headers=h,
                   files={"file": ("مفروم.png", PNG, "image/png")})
    assert r.status_code == 204, r.text
    g = client.get(f"{MENU}/items/{item_id}/image", headers=h)
    assert g.status_code == 200
    assert g.content == PNG and g.headers["content-type"].startswith("image/png")
    row = db_session.execute(select(MenuItemImage)).scalar_one()
    row.telegram_file_id = "old-file-id"
    db_session.commit()
    client.put(f"{MENU}/items/{item_id}/image", headers=h,
               files={"file": ("مفروم.png", PNG + b"2", "image/png")})
    db_session.expire_all()
    row = db_session.execute(select(MenuItemImage)).scalar_one()
    assert row.telegram_file_id is None and row.image == PNG + b"2"


def test_image_type_and_size_and_missing(client, two_businesses):
    h = auth_headers(two_businesses["a"])
    item_id = _mk_item(client, h)
    r = client.put(f"{MENU}/items/{item_id}/image", headers=h,
                   files={"file": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 415
    r = client.put(f"{MENU}/items/{item_id}/image", headers=h,
                   files={"file": ("big.png", b"x" * (5 * 1024 * 1024 + 1), "image/png")})
    assert r.status_code == 413
    assert client.get(f"{MENU}/items/{item_id}/image", headers=h).status_code == 404


def test_import_dry_run_and_apply_and_reports(tmp_path, two_businesses, db_session):
    biz = str(two_businesses["a"])
    from app.menu_import import import_menu_csv
    # same item name in two categories -> filename alone is ambiguous
    csv = ("category,item,variant,price,sold_by,unit_label,aliases,kind\n"
           "اللحوم,مفروم احمر,سعر 1,450.00,weight,كيلو,,\n"
           "الضاني,مفروم احمر,سعر 1,460.00,weight,كيلو,,\n"
           "اللحوم,كفتة,سعر 1,430.00,weight,كيلو,,\n")
    import_menu_csv(db_session, biz, csv.encode("utf-8"), dry_run=False)
    db_session.flush()
    (tmp_path / "مفروم احمر.png").write_bytes(PNG)   # ambiguous (two categories)
    (tmp_path / "لا يوجد.png").write_bytes(PNG)      # no item
    (tmp_path / "كفتة.png").write_bytes(PNG)         # unique match
    assert import_run(biz, str(tmp_path), apply=False, session=db_session) == 1  # problems reported
    assert db_session.execute(select(MenuItemImage)).scalars().all() == []
    (tmp_path / "لا يوجد.png").unlink()
    (tmp_path / "مفروم احمر.png").unlink()
    assert import_run(biz, str(tmp_path), apply=True, session=db_session) == 0
    rows = db_session.execute(select(MenuItemImage)).scalars().all()
    assert len(rows) == 1 and rows[0].image == PNG and rows[0].content_type == "image/png"
