"""Menu CSV import (Phase 1, 1.5, spec F4): validation, dry run, all-or-nothing,
idempotency, price update audit, default-variant rule."""

from sqlalchemy import select

from app.menu_import import import_menu_csv, parse_menu_csv
from app.models import AuditEvent
from app.models_menu import MenuItem, MenuItemVariant

CSV = """category,item,variant,price,sold_by,unit_label,aliases,kind
اللحوم,مفروم احمر,سعر 1,450.00,weight,كيلو,مفروم|مفروم أحمر
اللحوم,مفروم احمر,سعر 2,430.00,weight,كيلو,
الفراخ,فراخ بلدي,كيلو,٢٢٠,weight,كيلو,
خدمات تصنيع وتجهيز اللحوم,فرم اللحمة,فرم,20,unit,,تقطيع,service
اللحوم,كبيبة شامي,سعر 1,,weight,كيلو,
"""


def _import(db, business, content=CSV, dry_run=False):
    return import_menu_csv(db, business, content.encode("utf-8"), dry_run=dry_run)


def test_happy_path_creates_items_variants_aliases(db_session, two_businesses):
    r = _import(db_session, two_businesses["a"])
    assert r.errors == [] and (r.created, r.updated, r.unchanged) == (5, 0, 0)
    items = {i.name: i for i in db_session.scalars(select(MenuItem))}
    mafroom = items["مفروم احمر"]
    variants = db_session.scalars(select(MenuItemVariant).where(
        MenuItemVariant.item_id == mafroom.id).order_by(MenuItemVariant.sort_order)).all()
    # first row of the item (file order) is the default
    assert [v.is_default for v in variants] == [True, False]
    assert {a.alias for a in mafroom.aliases} == {"مفروم", "مفروم أحمر"}
    # Arabic-Indic digits parsed; empty price is NULL ("ask the cashier")
    farag = items["فراخ بلدي"]
    assert farag.variants[0].price == 220
    assert items["كبيبة شامي"].variants[0].price is None
    assert items["فرم اللحمة"].kind == "service"


def test_dry_run_writes_nothing(db_session, two_businesses):
    r = _import(db_session, two_businesses["a"], dry_run=True)
    assert r.created == 5
    assert db_session.scalars(select(MenuItem)).all() == []
    assert db_session.scalars(select(AuditEvent).where(
        AuditEvent.entity == "menu_import")).all() == []


def test_validation_is_all_or_nothing(db_session, two_businesses):
    bad = CSV + "اللحوم,بُرنيط,سعر 1,abc,weight,,\n"  # bad price + missing unit_label
    r = _import(db_session, two_businesses["a"], bad)
    assert {e.column for e in r.errors} >= {"price", "unit_label"}
    assert db_session.scalars(select(MenuItem)).all() == []  # nothing written


def test_unknown_column_and_missing_required_are_errors():
    _, errors = parse_menu_csv(b"category,item,variant,price,sold_by,zz\na,b,c,1,unit\n")
    assert any(e.column == "zz" and "unknown" in e.reason for e in errors)
    _, errors = parse_menu_csv(b"category,item,price,sold_by\na,b,1,unit\n")
    assert any(e.column == "variant" for e in errors)
    rows, errors = parse_menu_csv("category,item,variant,price,sold_by\na,b,c,1,unit\n"
                                  .encode("utf-8-sig"))  # BOM accepted
    assert errors == [] and len(rows) == 1


def test_duplicate_key_and_conflicting_sold_by_rejected():
    content = ("category,item,variant,price,sold_by,unit_label\n"
               "a,b,c,1,unit,\n"
               " a , b ,c,2,unit,\n"  # same natural key after normalization
               "a,b,d,3,weight,كيلو\n")   # same item, different sold_by
    rows, errors = parse_menu_csv(content.encode())
    assert any("duplicate" in e.reason for e in errors)
    assert any(e.column == "sold_by" and "conflicts" in e.reason for e in errors)


def test_reimport_is_idempotent_and_price_change_updates(db_session, two_businesses):
    biz = two_businesses["a"]
    assert _import(db_session, biz).created == 5
    again = _import(db_session, biz)
    assert (again.created, again.updated, again.unchanged) == (0, 0, 5)
    changed = CSV.replace("450.00", "460.00")
    r = _import(db_session, biz, changed)
    assert (r.created, r.updated, r.unchanged) == (0, 1, 4)
    audits = db_session.scalars(select(AuditEvent).where(
        AuditEvent.entity == "menu_import").order_by(AuditEvent.id)).all()
    assert len(audits) == 3 and len(audits[0].payload["sha256"]) == 64
    upd = [c for c in audits[-1].payload["changes"] if c["action"] == "update"]
    assert upd and upd[0]["old"] == "450.00" and upd[0]["new"] == "460.00"
    # the default variant is unchanged by the re-import
    item = db_session.scalar(select(MenuItem).where(MenuItem.name == "مفروم احمر"))
    assert sum(v.is_default for v in item.variants) == 1
