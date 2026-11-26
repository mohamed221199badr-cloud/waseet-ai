"""Orders. Lifecycle: draft -> pending -> confirmed | rejected.

Prices are resolved when an item is added (customer price wins over catalog price)
and frozen on the item. Stock is deducted only when the order is confirmed.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Product
from app.models_orders import Order, OrderItem
from app.repositories import customers as customers_repo
from app.repositories import inventory


class OrderStateError(Exception):
    """The requested transition is not allowed from the order's current status."""


class ProductUnavailable(Exception):
    """Product missing (or in another business) or inactive."""


class DuplicateItem(Exception):
    pass


def list_orders(
    db: Session, business_id: uuid.UUID, status: str | None = None,
    customer_id: uuid.UUID | None = None,
) -> list[Order]:
    stmt = select(Order).where(Order.business_id == business_id)
    if status:
        stmt = stmt.where(Order.status == status)
    if customer_id:
        stmt = stmt.where(Order.customer_id == customer_id)
    return list(db.scalars(stmt.order_by(Order.created_at.desc())))


def get_order(
    db: Session, business_id: uuid.UUID, order_id: uuid.UUID, lock: bool = False
) -> Order | None:
    stmt = select(Order).where(Order.business_id == business_id, Order.id == order_id)
    if lock:
        stmt = stmt.with_for_update()
    return db.scalar(stmt)


def create_order(db: Session, business_id: uuid.UUID, data: dict) -> Order:
    order = Order(business_id=business_id, status="draft", total=Decimal("0"), **data)
    db.add(order)
    db.flush()
    db.refresh(order)
    return order


def resolve_price(
    db: Session, business_id: uuid.UUID, customer_id: uuid.UUID, product: Product
) -> tuple[Decimal, str]:
    special = customers_repo.get_customer_price(db, business_id, customer_id, product.id)
    if special is not None:
        return Decimal(special.price), "customer"
    return Decimal(product.price), "catalog"


def recompute_total(db: Session, order: Order) -> None:
    db.flush()
    total = db.scalar(
        select(func.coalesce(func.sum(OrderItem.line_total), 0)).where(
            OrderItem.business_id == order.business_id, OrderItem.order_id == order.id
        )
    )
    order.total = Decimal(total)
    db.flush()
    db.refresh(order)


def _require_draft(order: Order) -> None:
    if order.status != "draft":
        raise OrderStateError(f"order is {order.status}; only draft orders can be edited")


def add_item(
    db: Session, order: Order, product_id: uuid.UUID, quantity: int
) -> OrderItem:
    _require_draft(order)
    product = db.scalar(
        select(Product).where(
            Product.business_id == order.business_id, Product.id == product_id
        )
    )
    if product is None or not product.is_active:
        raise ProductUnavailable(str(product_id))
    exists = db.scalar(
        select(OrderItem.id).where(
            OrderItem.order_id == order.id, OrderItem.product_id == product_id
        )
    )
    if exists:
        raise DuplicateItem(str(product_id))
    price, source = resolve_price(db, order.business_id, order.customer_id, product)
    item = OrderItem(
        business_id=order.business_id, order_id=order.id, product_id=product_id,
        quantity=quantity, unit_price=price, price_source=source,
        line_total=price * quantity,
    )
    db.add(item)
    recompute_total(db, order)
    return item


def remove_item(db: Session, order: Order, item_id: uuid.UUID) -> OrderItem | None:
    _require_draft(order)
    item = db.scalar(
        select(OrderItem).where(
            OrderItem.business_id == order.business_id,
            OrderItem.order_id == order.id,
            OrderItem.id == item_id,
        )
    )
    if item is None:
        return None
    db.delete(item)
    recompute_total(db, order)
    return item


def submit(db: Session, order: Order) -> Order:
    _require_draft(order)
    if any(e.status == "open" for e in order.exceptions):
        raise OrderStateError("order has unresolved exceptions; resolve or dismiss them first")
    if not order.items and not order.lines:
        raise OrderStateError("cannot submit an empty order")
    order.status = "awaiting_confirmation"
    db.flush()
    return order


def confirm(db: Session, order: Order) -> Order:
    """awaiting_confirmation -> confirmed. Locks products in id order (no deadlocks), deducts stock
    atomically; if any line lacks stock nothing changes."""
    if order.status != "awaiting_confirmation":
        raise OrderStateError(
            f"order is {order.status}; only awaiting_confirmation orders can be confirmed")
    for item in sorted(order.items, key=lambda i: str(i.product_id)):
        product = inventory.lock_product(db, order.business_id, item.product_id)
        inventory.apply_delta(
            db, product, -item.quantity, "order_confirmed", order_id=order.id
        )
    order.status = "confirmed"
    order.decided_at = datetime.now(timezone.utc)
    db.flush()
    return order


def reject(db: Session, order: Order, reason: str | None) -> Order:
    if order.status != "awaiting_confirmation":
        raise OrderStateError(
            f"order is {order.status}; only awaiting_confirmation orders can be rejected")
    order.status = "rejected"
    order.rejection_reason = reason
    order.decided_at = datetime.now(timezone.utc)
    db.flush()
    return order
