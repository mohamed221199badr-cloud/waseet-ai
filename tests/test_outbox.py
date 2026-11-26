"""Phase 3 (b): the notification outbox (v3 C1/C3).

dedupe_key shape and uniqueness (racing dispatchers), occurrence keeping repeated
events (redelivery -> ready again) from being swallowed, transition-driven emits
(customer-facing events + failed_delivery to the manager role group)."""

from sqlalchemy import select

from app.models_orders import NotificationOutbox, OrderStatusHistory
from app.services import outbox
from app.services.order_status import transition
from tests.test_migrations import _alembic, scratch_url  # noqa: F401
from tests.test_order_flow import world  # noqa: F401


def _rows(db, order, event=None):
    q = select(NotificationOutbox).where(NotificationOutbox.order_id == order.id)
    if event:
        q = q.where(NotificationOutbox.event == event)
    return db.scalars(q.order_by(NotificationOutbox.created_at)).all()


def test_confirm_queues_one_customer_notification(db_session, world):  # noqa: F811
    o = world["order"]
    transition(db_session, o, "awaiting_confirmation", actor_type="customer")
    transition(db_session, o, "confirmed", actor_type="user",
               role="cashier", actor_id=world["users"]["cashier"].id)
    rows = _rows(db_session, o, "order.confirmed")
    assert len(rows) == 1
    row = rows[0]
    assert row.recipient_type == "customer"
    assert row.recipient_id == str(world["cust"].id)
    assert row.channel == "telegram" and row.status == "pending"
    hist = db_session.scalars(select(OrderStatusHistory).where(
        OrderStatusHistory.order_id == o.id,
        OrderStatusHistory.to_status == "confirmed")).one()
    assert row.occurrence == hist.id
    assert row.dedupe_key == outbox.build_dedupe_key(
        "order.confirmed", o.id, "customer", str(world["cust"].id),
        "telegram", hist.id)


def test_emit_with_same_key_is_a_noop(db_session, world):  # noqa: F811
    o = world["order"]
    assert outbox.emit(db_session, world["biz"], "order.confirmed", o.id,
                       "customer", str(world["cust"].id)) is True
    assert outbox.emit(db_session, world["biz"], "order.confirmed", o.id,
                       "customer", str(world["cust"].id)) is False
    assert len(_rows(db_session, o)) == 1


def test_dedupe_key_format(db_session, world):  # noqa: F811
    o = world["order"]
    key = outbox.build_dedupe_key("order.confirmed", o.id, "customer", "abc",
                                  "telegram", None)
    assert key == f"order.confirmed:{o.id}:customer:abc:telegram:0"


def test_redelivery_repeat_is_not_swallowed(db_session, world):  # noqa: F811
    o = world["order"]
    transition(db_session, o, "awaiting_confirmation", actor_type="customer")
    transition(db_session, o, "confirmed", actor_type="user",
               role="cashier", actor_id=world["users"]["cashier"].id)
    k, d = world["users"]["kitchen"], world["users"]["delivery"]
    transition(db_session, o, "preparing", actor_type="user", role="kitchen", actor_id=k.id)
    transition(db_session, o, "ready", actor_type="user", role="kitchen", actor_id=k.id)
    transition(db_session, o, "out_for_delivery", actor_type="user",
               role="delivery", actor_id=d.id)
    transition(db_session, o, "failed_delivery", actor_type="user", role="delivery",
               actor_id=d.id, reason="customer_refused_new_total")
    transition(db_session, o, "ready", actor_type="user",
               role="manager", actor_id=world["users"]["manager"].id)
    ready = _rows(db_session, o, "order.ready")
    assert len(ready) == 2  # one per occurrence, C1's whole point
    assert ready[0].occurrence != ready[1].occurrence
    # failed delivery routes to the manager role group
    mgr = _rows(db_session, o, "order.failed_delivery")
    assert len(mgr) == 1
    assert mgr[0].recipient_type == "role_group" and mgr[0].recipient_id == "manager"
    assert mgr[0].payload["reason"] == "customer_refused_new_total"


def test_non_customer_events_queue_nothing(db_session, world):  # noqa: F811
    o = world["order"]
    transition(db_session, o, "awaiting_confirmation", actor_type="customer")
    assert _rows(db_session, o, "order.awaiting_confirmation") == []
    transition(db_session, o, "confirmed", actor_type="user",
               role="cashier", actor_id=world["users"]["cashier"].id)
    transition(db_session, o, "preparing", actor_type="user",
               role="kitchen", actor_id=world["users"]["kitchen"].id)
    assert _rows(db_session, o, "order.preparing") == []
