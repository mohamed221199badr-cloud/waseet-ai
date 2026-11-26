"""Phase 3 (a): the full order flow through OrderStatusService (v3 B5/F1/C2).

Transition table + permissions, confirmed_total baseline, weight adjustment with
cumulative thresholds, the re-confirmation block on dispatch, history attribution."""

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import User
from app.models_menu import MenuCategory, MenuItem, MenuItemVariant
from app.models_orders import Customer, Order, OrderLine, OrderStatusHistory
from app.services.order_status import (
    TransitionError,
    adjust_weight,
    assign_driver,
    reconfirm_total,
    transition,
)


def _user(db, biz, role, email):
    u = User(business_id=biz, full_name=role, email=email, password_hash="x", role=role)
    db.add(u)
    db.flush()
    return u


@pytest.fixture()
def world(db_session, two_businesses):  # noqa: F811
    biz = two_businesses["a"]
    cust = Customer(business_id=biz, name="عميل", phone="0100")
    db_session.add(cust)
    db_session.flush()
    cat = MenuCategory(business_id=biz, name="اللحوم")
    db_session.add(cat)
    db_session.flush()
    item = MenuItem(business_id=biz, category="اللحوم", name="مفروم", sold_by="weight",
                    unit_label="كيلو")
    db_session.add(item)
    db_session.flush()
    var = MenuItemVariant(business_id=biz, item_id=item.id, name="سعر 1",
                          price=Decimal("450.00"), is_default=True)
    db_session.add(var)
    db_session.flush()
    order = Order(business_id=biz, customer_id=cust.id, status="draft", channel="telegram")
    db_session.add(order)
    db_session.flush()
    line = OrderLine(business_id=biz, order_id=order.id, variant_id=var.id,
                     item_name="مفروم", variant_name="سعر 1", quantity=Decimal("2.000"),
                     unit_label="كيلو", unit_price=Decimal("450.00"), price_source="menu",
                     line_total=Decimal("900.00"))
    order.total = Decimal("900.00")
    db_session.add(line)
    users = {r: _user(db_session, biz, r, f"{r}@t.local")
             for r in ("cashier", "kitchen", "delivery", "manager", "storekeeper")}
    db_session.commit()
    return {"biz": biz, "cust": cust, "order": order, "line": line, "users": users}


def _history(db, order):
    return db.scalars(select(OrderStatusHistory).where(
        OrderStatusHistory.order_id == order.id).order_by(OrderStatusHistory.created_at)).all()


def test_customer_submits_draft(db_session, world):
    o = world["order"]
    transition(db_session, o, "awaiting_confirmation", actor_type="customer")
    assert o.status == "awaiting_confirmation"
    h = _history(db_session, o)
    assert (h[0].from_status, h[0].to_status, h[0].actor_type, h[0].actor_id) == (
        "draft", "awaiting_confirmation", "customer", None)


def test_customer_cannot_confirm(db_session, world):
    o = world["order"]
    transition(db_session, o, "awaiting_confirmation", actor_type="customer")
    with pytest.raises(TransitionError):
        transition(db_session, o, "confirmed", actor_type="customer")


def test_confirm_sets_confirmed_total_and_blocks_wrong_role(db_session, world):
    o = world["order"]
    transition(db_session, o, "awaiting_confirmation", actor_type="customer")
    with pytest.raises(TransitionError):  # storekeeper cannot confirm
        transition(db_session, o, "confirmed", actor_type="user",
                   role="storekeeper", actor_id=world["users"]["storekeeper"].id)
    transition(db_session, o, "confirmed", actor_type="user",
               role="cashier", actor_id=world["users"]["cashier"].id)
    assert o.status == "confirmed" and o.confirmed_total == Decimal("900.00")


def test_full_kitchen_delivery_path(db_session, world):
    o = world["order"]
    transition(db_session, o, "awaiting_confirmation", actor_type="customer")
    transition(db_session, o, "confirmed", actor_type="user",
               role="cashier", actor_id=world["users"]["cashier"].id)
    k = world["users"]["kitchen"]
    transition(db_session, o, "preparing", actor_type="user", role="kitchen", actor_id=k.id)
    transition(db_session, o, "ready", actor_type="user", role="kitchen", actor_id=k.id)
    d = world["users"]["delivery"]
    assign_driver(db_session, o, d.id, role="manager", actor_id=world["users"]["manager"].id)
    transition(db_session, o, "out_for_delivery", actor_type="user",
               role="delivery", actor_id=d.id)
    assert o.delivery_attempts == 1
    transition(db_session, o, "delivered", actor_type="user", role="delivery", actor_id=d.id)
    assert [h.to_status for h in _history(db_session, o)] == [
        "awaiting_confirmation", "confirmed", "preparing", "ready",
        "out_for_delivery", "delivered"]


def test_small_weight_adjust_applies_without_block(db_session, world):
    o = world["order"]
    transition(db_session, o, "awaiting_confirmation", actor_type="customer")
    transition(db_session, o, "confirmed", actor_type="user",
               role="cashier", actor_id=world["users"]["cashier"].id)
    # 2.05 kg: +22.50 EGP, under both thresholds (10% of 900 = 90, 50 EGP)
    adjust_weight(db_session, o, world["line"], Decimal("2.050"),
                  role="kitchen", actor_id=world["users"]["kitchen"].id)
    assert o.total == Decimal("922.50") and not o.requires_reconfirmation


def test_weight_adjust_over_threshold_blocks_dispatch_until_reconfirmed(db_session, world):
    o = world["order"]
    transition(db_session, o, "awaiting_confirmation", actor_type="customer")
    transition(db_session, o, "confirmed", actor_type="user",
               role="cashier", actor_id=world["users"]["cashier"].id)
    k = world["users"]["kitchen"]
    # 2.2 kg: +90 EGP, over the 50 EGP threshold
    adjust_weight(db_session, o, world["line"], Decimal("2.200"), role="kitchen", actor_id=k.id)
    assert o.total == Decimal("990.00") and o.requires_reconfirmation
    transition(db_session, o, "preparing", actor_type="user", role="kitchen", actor_id=k.id)
    transition(db_session, o, "ready", actor_type="user", role="kitchen", actor_id=k.id)
    with pytest.raises(TransitionError):  # dispatch blocked
        transition(db_session, o, "out_for_delivery", actor_type="user",
                   role="delivery", actor_id=world["users"]["delivery"].id)
    reconfirm_total(db_session, o, role="manager", actor_id=world["users"]["manager"].id)
    assert o.confirmed_total == Decimal("990.00") and not o.requires_reconfirmation
    transition(db_session, o, "out_for_delivery", actor_type="user",
               role="delivery", actor_id=world["users"]["delivery"].id)


def test_cumulative_adjustments_cross_threshold(db_session, world):
    o = world["order"]
    transition(db_session, o, "awaiting_confirmation", actor_type="customer")
    transition(db_session, o, "confirmed", actor_type="user",
               role="cashier", actor_id=world["users"]["cashier"].id)
    k = world["users"]["kitchen"]
    # two adjustments of +30 EGP each: each under 50, cumulative +60 over the limit
    adjust_weight(db_session, o, world["line"], Decimal("2.066"), role="kitchen", actor_id=k.id)
    assert not o.requires_reconfirmation
    adjust_weight(db_session, o, world["line"], Decimal("2.133"), role="kitchen", actor_id=k.id)
    assert o.requires_reconfirmation


def test_failed_delivery_redelivery_and_refusal_reason(db_session, world):
    o = world["order"]
    transition(db_session, o, "awaiting_confirmation", actor_type="customer")
    transition(db_session, o, "confirmed", actor_type="user",
               role="cashier", actor_id=world["users"]["cashier"].id)
    k = world["users"]["kitchen"]
    d = world["users"]["delivery"]
    transition(db_session, o, "preparing", actor_type="user", role="kitchen", actor_id=k.id)
    transition(db_session, o, "ready", actor_type="user", role="kitchen", actor_id=k.id)
    transition(db_session, o, "out_for_delivery", actor_type="user", role="delivery", actor_id=d.id)
    transition(db_session, o, "failed_delivery", actor_type="user", role="delivery",
               actor_id=d.id, reason="customer_refused_new_total")
    transition(db_session, o, "ready", actor_type="user",
               role="manager", actor_id=world["users"]["manager"].id)  # redelivery
    transition(db_session, o, "out_for_delivery", actor_type="user", role="delivery", actor_id=d.id)
    assert o.delivery_attempts == 2
    failed = [h for h in _history(db_session, o) if h.to_status == "failed_delivery"]
    assert failed[0].reason == "customer_refused_new_total"


def test_cancel_after_confirm_needs_manager(db_session, world):
    o = world["order"]
    transition(db_session, o, "awaiting_confirmation", actor_type="customer")
    transition(db_session, o, "confirmed", actor_type="user",
               role="cashier", actor_id=world["users"]["cashier"].id)
    with pytest.raises(TransitionError):
        transition(db_session, o, "cancelled", actor_type="user",
                   role="cashier", actor_id=world["users"]["cashier"].id)
    transition(db_session, o, "cancelled", actor_type="user",
               role="manager", actor_id=world["users"]["manager"].id)
    assert o.status == "cancelled"


def test_invalid_transition_rejected(db_session, world):
    with pytest.raises(TransitionError):
        transition(db_session, world["order"], "delivered", actor_type="user",
                   role="owner", actor_id=uuid.uuid4())
