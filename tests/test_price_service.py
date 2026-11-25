"""PriceService (Phase 1, 1.7, spec B4): the four named resolution tests."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models_menu import MenuCategory, MenuItem, MenuItemVariant, PriceOverride
from app.models_orders import Customer, Order, OrderException
from app.price_service import resolve_price

NOW = datetime(2026, 11, 26, 12, 0, )


@pytest.fixture
def variant(db_session, two_businesses):
    biz = two_businesses["a"]
    db_session.add(MenuCategory(business_id=biz, name="اللحوم"))
    db_session.flush()
    item = MenuItem(business_id=biz, category="اللحوم", name="كبيبة شامي",
                    sold_by="weight", unit_label="كيلو")
    db_session.add(item)
    db_session.flush()
    v = MenuItemVariant(business_id=biz, item_id=item.id, name="سعر 1", price=None,
                        is_default=True)
    db_session.add(v)
    db_session.flush()
    return v


def _override(db, variant, **kw):
    o = PriceOverride(business_id=variant.business_id, variant_id=variant.id,
                      price=kw.pop("price"), **kw)
    db.add(o)
    db.flush()
    return o


def test_null_base_with_customer_override_resolves_no_exception(db_session, variant,
                                                                two_businesses):
    cust = Customer(business_id=variant.business_id, name="عميل", phone="0100")
    db_session.add(cust)
    db_session.flush()
    _override(db_session, variant, price="100.00", scope="customer",
              customer_id=cust.id)
    r = resolve_price(db_session, variant, customer_id=cust.id, at=NOW)
    assert r.source == "override" and str(r.price) == "100.00"
    assert db_session.scalars(select(OrderException)).all() == []


def test_null_base_with_segment_override(db_session, variant):
    _override(db_session, variant, price="95.00", scope="segment", segment="جملة")
    r = resolve_price(db_session, variant, segment="جملة", at=NOW)
    assert r.source == "override" and str(r.price) == "95.00"
    # a customer outside the segment still gets nothing
    assert resolve_price(db_session, variant, segment="تجزئة", at=NOW).price is None


def test_null_base_no_override_returns_none_and_creates_exception(db_session, variant,
                                                                  two_businesses):
    cust = Customer(business_id=variant.business_id, name="عميل", phone="0101")
    db_session.add(cust)
    db_session.flush()
    order = Order(business_id=variant.business_id, customer_id=cust.id)
    db_session.add(order)
    db_session.flush()
    r = resolve_price(db_session, variant, at=NOW, order_id=order.id)
    assert r.price is None and r.source == "none"
    exc = db_session.scalars(select(OrderException)).one()
    assert exc.reason == "price_required" and exc.status == "open"
    assert exc.order_id == order.id


def test_override_expired_falls_back_to_null(db_session, variant):
    _override(db_session, variant, price="90.00", scope="segment", segment="جملة",
              ends_at=NOW - timedelta(days=1))
    r = resolve_price(db_session, variant, segment="جملة", at=NOW)
    assert r.price is None and r.source == "none"
    # and a not-yet-started override does not apply either
    _override(db_session, variant, price="90.00", scope="customer",
              customer_id=None, segment=None) if False else None
    v2 = variant
    o2 = PriceOverride(business_id=v2.business_id, variant_id=v2.id, price="88.00",
                       scope="segment", segment="vip",
                       starts_at=NOW + timedelta(days=1))
    db_session.add(o2)
    db_session.flush()
    assert resolve_price(db_session, v2, segment="vip", at=NOW).price is None
    # base price wins when it is set (source menu)
    variant.price = 80
    db_session.flush()
    assert resolve_price(db_session, variant, at=NOW).source == "menu"
