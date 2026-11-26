"""Phase 2 cutover: the chat agent on the restaurant menu tables.

Covers the cutover switch (menu present -> menu mode, absent -> legacy products),
single/multi-variant flows, weight fractions, the B4 price rules (override wins,
NULL price never quoted and never drafted), category listing, and tenant isolation."""

import itertools
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.config import settings
from app.models_menu import MenuCategory, MenuItem, MenuItemVariant, PriceOverride
from app.models_orders import Order, OrderItem, OrderLine
from app.telegram import service as svc
from tests.test_intake import shop  # noqa: F401
from tests.test_migrations import _alembic, scratch_url  # noqa: F401
from tests.test_telegram import link, upd

_ids = itertools.count(880000)


@pytest.fixture(autouse=True)
def _agent_on(monkeypatch):
    monkeypatch.setattr(settings, "chat_agent_enabled", True)
    monkeypatch.setattr(settings, "llm_mode", "off")


@pytest.fixture()
def linked(client, db_session, shop):  # noqa: F811
    link(db_session, 500, shop)
    # complete the onboarding profile (F2): the shop customer already has a phone
    from app.models_orders import Customer
    cust = db_session.scalar(
        select(Customer).where(Customer.business_id == shop["ids"]["a"]))
    cust.delivery_method = "pickup"
    db_session.commit()
    return shop


def _add_category(db, biz, name, sort=0):
    c = MenuCategory(business_id=biz, name=name, sort_order=sort)
    db.add(c)
    db.flush()
    return c


def _add_item(db, biz, category, name, variants, sold_by="unit", unit_label=None, kind="food"):
    """variants: list of (name, price|None, is_default)."""
    item = MenuItem(business_id=biz, category=category, name=name, kind=kind,
                    sold_by=sold_by, unit_label=unit_label)
    db.add(item)
    db.flush()
    vs = []
    for i, (vname, price, is_default) in enumerate(variants):
        v = MenuItemVariant(business_id=biz, item_id=item.id, name=vname, price=price,
                            is_default=is_default, sort_order=i)
        db.add(v)
        vs.append(v)
    db.commit()
    return item, vs


@pytest.fixture()
def menu(db_session, shop):  # noqa: F811
    biz = shop["ids"]["a"]
    _add_category(db_session, biz, "مصنعات وقطعات", 1)
    _add_category(db_session, biz, "اللحوم", 0)
    kofta, kofta_v = _add_item(db_session, biz, "مصنعات وقطعات", "كفتة حاتي",
                               [("عادي", Decimal("420.00"), True)])
    burger, burger_v = _add_item(db_session, biz, "مصنعات وقطعات", "برجر بلدي",
                                 [("سعر 1", Decimal("280.00"), True),
                                  ("سعر 2", Decimal("350.00"), False),
                                  ("سعر 3", Decimal("450.00"), False)])
    meat, meat_v = _add_item(db_session, biz, "اللحوم", "مفروم احمر",
                             [("عادي", Decimal("450.00"), True)],
                             sold_by="weight", unit_label="كيلو")
    kebiba, kebiba_v = _add_item(db_session, biz, "اللحوم", "كبيبة شامي",
                                 [("عادي", None, True)], sold_by="weight", unit_label="كيلو")
    return {"kofta": kofta, "burger": burger, "meat": meat, "kebiba": kebiba,
            "burger_v": burger_v, "kofta_v": kofta_v, "meat_v": meat_v, "kebiba_v": kebiba_v}


def _cbq(chat_id, data, msg_id=42):
    return {
        "update_id": next(_ids),
        "callback_query": {
            "id": f"cbq-{next(_ids)}", "data": data,
            "from": {"id": chat_id, "is_bot": False},
            "message": {"message_id": msg_id,
                        "chat": {"id": chat_id, "type": "private"}},
        },
    }


def _finish_checkout(db, chat_id=500, method="pickup"):
    """A typed «تمام» starts checkout (checkout spec 2026-11-26): pick the delivery
    type, then final confirm. The customer has a phone, so the phone step is skipped."""
    from app.chat import callbacks as cb_codec
    svc.handle_update(db, _cbq(chat_id, cb_codec.build("ckdel", method)))
    return svc.handle_update(db, _cbq(chat_id, cb_codec.build("ckok")))[0]


def _lines(db, order):
    return db.scalars(select(OrderLine).where(OrderLine.order_id == order.id)).all()


def test_cutover_switch_menu_vs_legacy(db_session, linked, menu):  # noqa: F811
    out = svc.handle_update(db_session, upd(500, "سعر كفتة حاتي"))
    assert "كفتة حاتي" in out[0].text and "420" in out[0].text      # menu answer
    # business b has no menu rows: its chats stay on the legacy products path


def test_single_variant_order_flow(db_session, linked, menu):  # noqa: F811
    out = svc.handle_update(db_session, upd(500, "كفتة حاتي"))
    assert "كام «كفتة حاتي»" in out[0].text
    out = svc.handle_update(db_session, upd(500, "2"))
    assert "2 × كفتة حاتي = 840.00 (السعر 420.00)" in out[0].text
    out = svc.handle_update(db_session, upd(500, "تمام"))
    assert "دليفري ولا استلام" in out[0].text       # checkout starts; no order yet
    assert db_session.scalar(select(Order)) is None
    out = _finish_checkout(db_session)
    assert "اتأكد" in out.text
    order = db_session.scalar(select(Order))
    assert order is not None and order.total == Decimal("840.00")
    assert order.status == "confirmed"
    lines = _lines(db_session, order)
    assert len(lines) == 1
    assert lines[0].item_name == "كفتة حاتي" and lines[0].variant_name == "عادي"
    assert lines[0].unit_price == Decimal("420.00") and lines[0].price_source == "menu"
    assert db_session.scalar(select(OrderItem)) is None   # menu mode never writes order_items


def test_multi_variant_asks_then_priced_from_db(db_session, linked, menu):  # noqa: F811
    out = svc.handle_update(db_session, upd(500, "برجر بلدي"))
    assert "برجر بلدي" in out[0].text
    assert "سعر 1 - 280.00ج" in out[0].text and "سعر 3 - 450.00ج" in out[0].text
    out = svc.handle_update(db_session, upd(500, "2"))          # pick سعر 2
    assert "كام «برجر بلدي (سعر 2)»" in out[0].text
    out = svc.handle_update(db_session, upd(500, "3"))
    assert "3 × برجر بلدي (سعر 2) = 1050.00 (السعر 350.00)" in out[0].text
    svc.handle_update(db_session, upd(500, "تمام"))
    _finish_checkout(db_session)
    lines = _lines(db_session, db_session.scalar(select(Order)))
    assert lines[0].variant_name == "سعر 2" and lines[0].line_total == Decimal("1050.00")


def test_variant_picked_by_name(db_session, linked, menu):  # noqa: F811
    svc.handle_update(db_session, upd(500, "برجر بلدي"))
    out = svc.handle_update(db_session, upd(500, "سعر 3"))
    assert "كام «برجر بلدي (سعر 3)»" in out[0].text


def test_null_price_never_quoted_never_drafted(db_session, linked, menu):  # noqa: F811
    out = svc.handle_update(db_session, upd(500, "كبيبة شامي"))
    assert "الكاشير" in out[0].text and "سعر" in out[0].text
    assert db_session.scalar(select(Order)) is None


def test_null_price_with_override_resolves(db_session, linked, shop, menu):  # noqa: F811
    from app.models_orders import Customer
    customer = db_session.scalar(select(Customer).where(
        Customer.business_id == shop["ids"]["a"]))
    db_session.add(PriceOverride(
        business_id=shop["ids"]["a"], variant_id=menu["kebiba_v"][0].id, scope="customer",
        customer_id=customer.id, price=Decimal("300.00")))
    db_session.commit()
    svc.handle_update(db_session, upd(500, "كبيبة شامي"))
    out = svc.handle_update(db_session, upd(500, "2"))
    assert "السعر 300.00" in out[0].text
    svc.handle_update(db_session, upd(500, "تمام"))
    _finish_checkout(db_session)
    lines = _lines(db_session, db_session.scalar(select(Order)))
    assert lines[0].price_source == "override" and lines[0].unit_price == Decimal("300.00")


def test_expired_override_falls_back_to_base(db_session, linked, shop, menu):  # noqa: F811
    from datetime import datetime, timedelta

    from app.models_orders import Customer
    customer = db_session.scalar(select(Customer).where(
        Customer.business_id == shop["ids"]["a"]))
    db_session.add(PriceOverride(
        business_id=shop["ids"]["a"], variant_id=menu["kofta_v"][0].id, scope="customer",
        customer_id=customer.id, price=Decimal("1.00"),
        ends_at=datetime.utcnow() - timedelta(hours=1)))
    db_session.commit()
    svc.handle_update(db_session, upd(500, "كفتة حاتي"))
    out = svc.handle_update(db_session, upd(500, "1"))
    assert "السعر 420.00" in out[0].text     # expired override ignored


def test_weight_item_accepts_fraction(db_session, linked, menu):  # noqa: F811
    svc.handle_update(db_session, upd(500, "مفروم احمر"))
    out = svc.handle_update(db_session, upd(500, "2.5"))
    assert "2.5 × مفروم احمر = 1125.00 (السعر 450.00)" in out[0].text
    svc.handle_update(db_session, upd(500, "تمام"))
    _finish_checkout(db_session)
    lines = _lines(db_session, db_session.scalar(select(Order)))
    assert lines[0].quantity == Decimal("2.500") and lines[0].unit_label == "كيلو"


def test_catalog_lists_menu_categories(db_session, linked, menu):  # noqa: F811
    out = svc.handle_update(db_session, upd(500, "الأصناف"))
    assert "اللحوم" in out[0].text and "مصنعات وقطعات" in out[0].text
    assert "كفتة حاتي" in out[0].text and "مفروم احمر" in out[0].text


def test_tenant_isolation_menu(db_session, linked, shop, menu):  # noqa: F811
    # business b plants a same-named item at a different price: chat 500 (business a)
    # must only ever see its own menu
    _add_category(db_session, shop["ids"]["b"], "مصنعات وقطعات", 1)
    _add_item(db_session, shop["ids"]["b"], "مصنعات وقطعات", "كفتة حاتي",
              [("عادي", Decimal("999.00"), True)])
    out = svc.handle_update(db_session, upd(500, "سعر كفتة حاتي"))
    assert "420" in out[0].text and "999" not in out[0].text


def test_kilo_word_means_one_kilo(db_session, linked, menu):  # noqa: F811
    out = svc.handle_update(db_session, upd(500, "كيلو مفروم احمر"))
    assert "1 × مفروم احمر = 450.00" in out[0].text
    svc.handle_update(db_session, upd(500, "تمام"))
    _finish_checkout(db_session)
    lines = _lines(db_session, db_session.scalar(select(Order)))
    assert lines[0].quantity == Decimal("1.000")


def test_half_kilo_phrase(db_session, linked, menu):  # noqa: F811
    out = svc.handle_update(db_session, upd(500, "نص كيلو مفروم احمر"))
    assert "0.5 × مفروم احمر = 225.00" in out[0].text


def test_bare_kilo_answers_qty_question(db_session, linked, menu):  # noqa: F811
    svc.handle_update(db_session, upd(500, "مفروم احمر"))
    out = svc.handle_update(db_session, upd(500, "كيلو"))
    assert "1 × مفروم احمر = 450.00" in out[0].text


def test_control_word_adds_no_line(db_session, linked, menu):  # noqa: F811
    # a stray "تمام" while a quantity question is open must not become an order
    # line (the fuzzy matcher offered التوماموك for تمام on the real menu)
    from app.models_orders import ChatSession
    svc.handle_update(db_session, upd(500, "مفروم احمر"))
    out = svc.handle_update(db_session, upd(500, "تمام"))
    assert "كام «مفروم احمر»" in out[0].text or "ما فهمتش الكمية" in out[0].text
    s = db_session.scalar(select(ChatSession).where(ChatSession.chat_id == 500))
    assert len(s.draft["lines"]) == 1
    out = svc.handle_update(db_session, upd(500, "2"))
    assert "2 × مفروم احمر = 900.00" in out[0].text


def test_same_name_two_categories_shows_category(db_session, linked, menu):  # noqa: F811
    # كفتة حاتي exists in two categories on the real menu: the choice list must
    # say which is which, not show the same name twice
    _add_item(db_session, menu["kofta"].business_id, "اللحوم", "كفتة حاتي",
              [("عادي", Decimal("400.00"), True)])
    out = svc.handle_update(db_session, upd(500, "٢ كفتة حاتي"))
    assert "كفتة حاتي (مصنعات وقطعات)" in out[0].text
    assert "كفتة حاتي (اللحوم)" in out[0].text
    out = svc.handle_update(db_session, upd(500, "1"))
    assert "2 × كفتة حاتي = 840.00" in out[0].text
