"""OrderStatusService (v3 B5/F1/C2): every order transition goes through here.

One place owns the transition table, the permission for each transition, the side
effects (confirmed_total, delivery_attempts, the re-confirmation block) and the
order_status_history row. actor_type is 'user' (staff), 'customer' (the bot/chat,
attributed through orders.customer_id) or 'system' (expiry and later outbox jobs).
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.models_menu import RestaurantSettings
from app.models_orders import Order, OrderLine, OrderStatusHistory
from app.permissions import ROLE_PERMISSIONS
from app.permissions import Permission as P


class TransitionError(Exception):
    """The requested transition is not allowed from the current status, or the
    actor lacks its permission, or a block (re-confirmation) is in force."""


# (from, to) -> permission. None = open to the customer actor (bot flow) and staff.
TRANSITIONS: dict[tuple[str, str], P | None] = {
    ("draft", "awaiting_confirmation"): None,  # bot submit, or cashier manual create
    ("awaiting_confirmation", "confirmed"): P.ORDER_CONFIRM,
    ("awaiting_confirmation", "rejected"): P.ORDER_REJECT,
    ("draft", "cancelled"): None,              # customer request or staff edit
    ("awaiting_confirmation", "cancelled"): P.ORDER_EDIT_LINES,
    ("confirmed", "preparing"): P.ORDER_ADVANCE_PREPARATION,
    ("preparing", "ready"): P.ORDER_ADVANCE_PREPARATION,
    ("ready", "out_for_delivery"): P.ORDER_ADVANCE_DELIVERY,
    ("out_for_delivery", "delivered"): P.ORDER_ADVANCE_DELIVERY,
    ("out_for_delivery", "failed_delivery"): P.ORDER_ADVANCE_DELIVERY,
    ("failed_delivery", "ready"): P.ORDER_ASSIGN_DRIVER,   # redelivery
    ("confirmed", "cancelled"): P.ORDER_CANCEL_AFTER_CONFIRM,
    ("failed_delivery", "cancelled"): P.ORDER_CANCEL_AFTER_CONFIRM,
}

CUSTOMER_TRANSITIONS = {("draft", "awaiting_confirmation"), ("draft", "cancelled")}
REASON_CUSTOMER_REFUSED = "customer_refused_new_total"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _allowed(role: str | None, perm: P | None, actor_type: str) -> bool:
    if actor_type == "customer":
        return perm is None  # customers only do the open (permission-free) transitions
    if actor_type == "system":
        return True  # internal automation; already gated by the calling code path
    # staff: open transitions (perm None) are allowed for any staff role; guarded ones
    # need the permission on the caller's role
    return perm is None or (role is not None and perm in ROLE_PERMISSIONS.get(role, frozenset()))


def transition(db: Session, order: Order, to_status: str, *,
               actor_type: str, role: str | None = None,
               actor_id: uuid.UUID | None = None, reason: str | None = None) -> Order:
    """Move an order along the v3 transition table. Validates, applies side effects,
    writes order_status_history and an audit event. The caller commits."""
    key = (order.status, to_status)
    if key not in TRANSITIONS:
        raise TransitionError(f"{order.status} -> {to_status} is not a valid transition")
    perm = TRANSITIONS[key]
    if actor_type == "customer" and key not in CUSTOMER_TRANSITIONS:
        raise TransitionError(f"customers cannot do {order.status} -> {to_status}")
    if not _allowed(role, perm, actor_type):
        raise TransitionError(f"role {role!r} lacks permission for {order.status} -> {to_status}")
    if to_status == "out_for_delivery" and order.requires_reconfirmation:
        raise TransitionError(
            "total changed after confirmation; a cashier/manager must re-confirm first")

    from_status = order.status
    if to_status == "confirmed":
        order.confirmed_total = order.total  # B5: the delta baseline the customer agreed to
        order.decided_at = _now()
    if to_status == "rejected":
        order.decided_at = _now()
    if to_status == "out_for_delivery":
        order.delivery_attempts += 1
    order.status = to_status
    db.add(OrderStatusHistory(
        business_id=order.business_id, order_id=order.id,
        from_status=from_status, to_status=to_status,
        actor_type=actor_type, actor_id=actor_id if actor_type == "user" else None,
        reason=reason))
    write_audit(db, business_id=order.business_id, entity="order", entity_id=str(order.id),
                action="status_change",
                payload={"from": from_status, "to": to_status, "actor_type": actor_type,
                         "actor_id": str(actor_id) if actor_id else None, "reason": reason})
    db.flush()
    return order


def assign_driver(db: Session, order: Order, driver_user_id: uuid.UUID, *,
                  role: str, actor_id: uuid.UUID) -> Order:
    if P.ORDER_ASSIGN_DRIVER not in ROLE_PERMISSIONS.get(role, frozenset()):
        raise TransitionError(f"role {role!r} lacks permission to assign a driver")
    order.assigned_driver_id = driver_user_id
    write_audit(db, business_id=order.business_id, entity="order", entity_id=str(order.id),
                action="assign_driver",
                payload={"driver": str(driver_user_id), "by": str(actor_id)})
    db.flush()
    return order


def adjust_weight(db: Session, order: Order, line: OrderLine, new_quantity: Decimal, *,
                  role: str, actor_id: uuid.UUID) -> Order:
    """B5: weight correction on confirmed/preparing orders. Recomputes the line and the
    total in the same transaction; crossing the business thresholds (measured against
    confirmed_total, cumulatively) sets requires_reconfirmation, which blocks dispatch."""
    if order.status not in ("confirmed", "preparing"):
        raise TransitionError(f"order is {order.status}; weight adjusts only confirmed..preparing")
    if P.ORDER_ADJUST_WEIGHT not in ROLE_PERMISSIONS.get(role, frozenset()):
        raise TransitionError(f"role {role!r} lacks permission to adjust weight")
    if new_quantity <= 0:
        raise TransitionError("quantity must be positive")
    line.quantity = new_quantity
    line.line_total = (line.unit_price * new_quantity).quantize(Decimal("0.01"))
    order.total = sum((ln.line_total for ln in order.lines), Decimal("0")).quantize(Decimal("0.01"))
    baseline = order.confirmed_total if order.confirmed_total is not None else order.total
    settings = db.scalar(select(RestaurantSettings).where(
        RestaurantSettings.business_id == order.business_id))
    pct = settings.reconfirm_pct if settings else Decimal("10")
    abs_egp = settings.reconfirm_abs_egp if settings else Decimal("50")
    delta = abs(order.total - baseline)
    if delta > (baseline * pct / 100).quantize(Decimal("0.01")) or delta > abs_egp:
        order.requires_reconfirmation = True
    write_audit(db, business_id=order.business_id, entity="order", entity_id=str(order.id),
                action="adjust_weight",
                payload={"line": str(line.id), "qty": str(new_quantity),
                         "total": str(order.total), "confirmed_total": str(baseline),
                         "requires_reconfirmation": order.requires_reconfirmation,
                         "by": str(actor_id)})
    db.flush()
    return order


def reconfirm_total(db: Session, order: Order, *, role: str, actor_id: uuid.UUID) -> Order:
    """B5: a cashier/manager records that the customer agreed to the new total."""
    if P.ORDER_RECONFIRM_TOTAL not in ROLE_PERMISSIONS.get(role, frozenset()):
        raise TransitionError(f"role {role!r} lacks permission to re-confirm the total")
    order.confirmed_total = order.total
    order.requires_reconfirmation = False
    write_audit(db, business_id=order.business_id, entity="order", entity_id=str(order.id),
                action="reconfirm_total",
                payload={"confirmed_total": str(order.total), "by": str(actor_id)})
    db.flush()
    return order
