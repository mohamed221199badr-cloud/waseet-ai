"""Phase 3 (a): COD payment actions (v3 F1 payment transitions + F5 refund policy)."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import AuditEvent
from app.models_menu import RestaurantSettings
from app.services.payments import (
    PaymentError,
    collect_payment,
    reconcile_payment,
    refund_payment,
    refunded_so_far,
)
from tests.test_order_flow import world  # noqa: F401  (fixture)


def _audits(db, order, action):
    return db.scalars(select(AuditEvent).where(
        AuditEvent.entity == "order", AuditEvent.entity_id == str(order.id),
        AuditEvent.action == action)).all()


def test_collect_marks_paid_and_audits(db_session, world):  # noqa: F811
    o = world["order"]
    d = world["users"]["delivery"]
    collect_payment(db_session, o, role="delivery", actor_id=d.id)
    assert o.payment_status == "collected"
    ev = _audits(db_session, o, "payment.collect")
    assert len(ev) == 1 and ev[0].payload["amount"] == "900.00"
    assert ev[0].actor_id == d.id


def test_collect_needs_permission(db_session, world):  # noqa: F811
    o = world["order"]
    k = world["users"]["kitchen"]
    with pytest.raises(PaymentError):
        collect_payment(db_session, o, role="kitchen", actor_id=k.id)
    assert o.payment_status == "pending"


def test_collect_twice_refused(db_session, world):  # noqa: F811
    o = world["order"]
    m = world["users"]["manager"]
    collect_payment(db_session, o, role="manager", actor_id=m.id)
    with pytest.raises(PaymentError):
        collect_payment(db_session, o, role="manager", actor_id=m.id)


def test_collect_on_cancelled_refused(db_session, world):  # noqa: F811
    o = world["order"]
    o.status = "cancelled"
    m = world["users"]["manager"]
    with pytest.raises(PaymentError):
        collect_payment(db_session, o, role="manager", actor_id=m.id)


def test_reconcile_only_after_collect(db_session, world):  # noqa: F811
    o = world["order"]
    c = world["users"]["cashier"]
    with pytest.raises(PaymentError):
        reconcile_payment(db_session, o, role="cashier", actor_id=c.id)
    collect_payment(db_session, o, role="cashier", actor_id=c.id)
    reconcile_payment(db_session, o, role="cashier", actor_id=c.id)
    assert len(_audits(db_session, o, "payment.reconcile")) == 1


def _collected(db, w):
    o = w["order"]
    m = w["users"]["manager"]
    collect_payment(db, o, role="manager", actor_id=m.id)
    db.flush()
    return o


def test_small_refund_by_cashier(db_session, world):  # noqa: F811
    o = _collected(db_session, world)
    c = world["users"]["cashier"]
    refund_payment(db_session, o, Decimal("80"), reason="quality_issue",
                   note="اللحمة وصلت بايظة", role="cashier", actor_id=c.id)
    assert o.payment_status == "collected"  # partially refunded: still collected
    ev = _audits(db_session, o, "payment.refund")[0]
    assert ev.payload["cumulative_refunded"] == "80"
    assert ev.payload["reason"] == "quality_issue"
    assert ev.payload["authorized_by"] == str(c.id)
    assert refunded_so_far(db_session, o) == Decimal("80")


def test_cumulative_refunds_cross_limit_need_manager(db_session, world):  # noqa: F811
    o = _collected(db_session, world)
    c = world["users"]["cashier"]
    m = world["users"]["manager"]
    refund_payment(db_session, o, Decimal("80"), reason="wrong_item",
                   note="صنف غلط", role="cashier", actor_id=c.id)
    # cumulative 160 > the 100 limit: the cashier's second 80 is refused
    with pytest.raises(PaymentError):
        refund_payment(db_session, o, Decimal("80"), reason="wrong_item",
                       note="صنف غلط تاني", role="cashier", actor_id=c.id)
    # a manager (PAYMENT_REFUND_LARGE) approves it, on behalf recorded
    refund_payment(db_session, o, Decimal("80"), reason="wrong_item",
                   note="صنف غلط تاني", role="manager", actor_id=c.id, authorized_by=m.id)
    ev = _audits(db_session, o, "payment.refund")[-1]
    assert ev.payload["cumulative_refunded"] == "160"
    assert ev.payload["authorized_by"] == str(m.id)
    assert refunded_so_far(db_session, o) == Decimal("160")


def test_refund_requires_reason_and_note(db_session, world):  # noqa: F811
    o = _collected(db_session, world)
    m = world["users"]["manager"]
    with pytest.raises(PaymentError):
        refund_payment(db_session, o, Decimal("10"), reason="because",
                       note="x", role="manager", actor_id=m.id)
    with pytest.raises(PaymentError):
        refund_payment(db_session, o, Decimal("10"), reason="other",
                       note="  ", role="manager", actor_id=m.id)


def test_refund_never_exceeds_collected(db_session, world):  # noqa: F811
    o = _collected(db_session, world)
    m = world["users"]["manager"]
    with pytest.raises(PaymentError):
        refund_payment(db_session, o, Decimal("901"), reason="other",
                       note="كله", role="manager", actor_id=m.id)


def test_full_refund_marks_refunded(db_session, world):  # noqa: F811
    o = _collected(db_session, world)
    m = world["users"]["manager"]
    refund_payment(db_session, o, Decimal("900"), reason="customer_refused",
                   note="رفض الاستلام", role="manager", actor_id=m.id)
    assert o.payment_status == "refunded"
    # nothing more to refund
    with pytest.raises(PaymentError):
        refund_payment(db_session, o, Decimal("1"), reason="other",
                       note="زيادة", role="manager", actor_id=m.id)


def test_refund_limit_comes_from_settings(db_session, world):  # noqa: F811
    db_session.add(RestaurantSettings(
        business_id=world["biz"], refund_limit_egp=Decimal("200")))
    db_session.flush()
    o = _collected(db_session, world)
    c = world["users"]["cashier"]
    # cumulative 160 is under the raised 200 limit: cashier can do it alone
    refund_payment(db_session, o, Decimal("160"), reason="other",
                   note="تعويض", role="cashier", actor_id=c.id)
    assert refunded_so_far(db_session, o) == Decimal("160")
