"""COD payment actions (v3 F1 payment transitions + F5 refund policy).

collect:    pending -> collected  (PAYMENT_COLLECT: delivery, cashier, manager)
reconcile:  cash counted against a collected order (PAYMENT_RECONCILE: cashier, manager)
refund:     cash handed back (PAYMENT_REFUND up to restaurant_settings.refund_limit_egp,
            PAYMENT_REFUND_LARGE above it). The limit applies to the CUMULATIVE refunded
            amount on the order, so two small refunds can together need a manager.

F5: the audit_events row is the refund record (order, amount, cumulative, reason,
actor_id, authorized_by, time) — payment_status alone is not the record. COD refunds
are cash; the online path arrives with a provider in Phase 6.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.models import AuditEvent
from app.models_menu import RestaurantSettings
from app.models_orders import Order
from app.permissions import ROLE_PERMISSIONS
from app.permissions import Permission as P

# v3 F5 requires a reason enum + note; v3 does not name the enum values, so this is
# the initial set (flagged in the phase report; easy to extend).
REFUND_REASONS = {"customer_refused", "wrong_item", "quality_issue", "other"}

DEFAULT_REFUND_LIMIT = Decimal("100")


class PaymentError(Exception):
    pass


def _has(role: str | None, perm: P) -> bool:
    return role is not None and perm in ROLE_PERMISSIONS.get(role, frozenset())


def _refund_limit(db: Session, business_id: uuid.UUID) -> Decimal:
    settings = db.scalar(select(RestaurantSettings).where(
        RestaurantSettings.business_id == business_id))
    if settings is None or settings.refund_limit_egp is None:
        return DEFAULT_REFUND_LIMIT
    return settings.refund_limit_egp


def refunded_so_far(db: Session, order: Order) -> Decimal:
    """Cumulative refunded amount on the order, from the F5 audit records."""
    events = db.scalars(select(AuditEvent).where(
        AuditEvent.business_id == order.business_id,
        AuditEvent.entity == "order", AuditEvent.entity_id == str(order.id),
        AuditEvent.action == "payment.refund")).all()
    return sum((Decimal(str(e.payload["amount"])) for e in events), Decimal("0"))


def collect_payment(db: Session, order: Order, *, role: str, actor_id: uuid.UUID) -> Order:
    """Cash collected from the customer (usually by the driver at delivery)."""
    if not _has(role, P.PAYMENT_COLLECT):
        raise PaymentError(f"role {role!r} lacks payment.collect")
    if order.payment_status != "pending":
        raise PaymentError(f"payment is {order.payment_status}; only pending can be collected")
    if order.status in ("cancelled", "rejected"):
        raise PaymentError(f"order is {order.status}; cannot collect payment")
    order.payment_status = "collected"
    write_audit(db, business_id=order.business_id, entity="order", entity_id=str(order.id),
                action="payment.collect", actor_id=actor_id,
                payload={"amount": str(order.total), "actor_id": str(actor_id)})
    db.flush()
    return order


def reconcile_payment(db: Session, order: Order, *, role: str, actor_id: uuid.UUID) -> Order:
    """Cash counted and reconciled against a collected order (day-end COD check)."""
    if not _has(role, P.PAYMENT_RECONCILE):
        raise PaymentError(f"role {role!r} lacks payment.reconcile")
    if order.payment_status != "collected":
        raise PaymentError(f"payment is {order.payment_status}; only collected can be reconciled")
    write_audit(db, business_id=order.business_id, entity="order", entity_id=str(order.id),
                action="payment.reconcile", actor_id=actor_id,
                payload={"amount": str(order.total), "actor_id": str(actor_id)})
    db.flush()
    return order


def refund_payment(db: Session, order: Order, amount: Decimal, *, reason: str, note: str,
                   role: str, actor_id: uuid.UUID,
                   authorized_by: uuid.UUID | None = None) -> Order:
    """Hand cash back. The permission depends on the CUMULATIVE refunded amount:
    at or under refund_limit_egp a cashier/manager does it; above it needs
    PAYMENT_REFUND_LARGE (manager/owner). authorized_by names the permission holder
    when a manager approves on a cashier's behalf (defaults to the actor)."""
    if reason not in REFUND_REASONS:
        raise PaymentError(f"reason must be one of {sorted(REFUND_REASONS)}")
    if not note or not note.strip():
        raise PaymentError("a refund note is required")
    if amount is None or amount <= 0:
        raise PaymentError("refund amount must be positive")
    if order.payment_status not in ("collected", "refunded"):
        raise PaymentError(f"payment is {order.payment_status}; only collected orders refund")
    prior = refunded_so_far(db, order)
    collected_total = order.confirmed_total if order.confirmed_total is not None else order.total
    if prior + amount > collected_total:
        raise PaymentError(
            f"refund {amount} exceeds the collected amount ({collected_total} total, "
            f"{prior} already refunded)")
    cumulative = prior + amount
    if cumulative <= _refund_limit(db, order.business_id):
        perm = P.PAYMENT_REFUND
    else:
        perm = P.PAYMENT_REFUND_LARGE
    if not _has(role, perm):
        raise PaymentError(f"role {role!r} lacks {perm.value} (cumulative refund {cumulative})")
    if cumulative >= collected_total:
        order.payment_status = "refunded"
    write_audit(db, business_id=order.business_id, entity="order", entity_id=str(order.id),
                action="payment.refund", actor_id=actor_id,
                payload={"amount": str(amount), "cumulative_refunded": str(cumulative),
                         "reason": reason, "note": note.strip(),
                         "actor_id": str(actor_id),
                         "authorized_by": str(authorized_by or actor_id)})
    db.flush()
    return order
