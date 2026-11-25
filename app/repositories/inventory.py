"""Stock changes. products.stock_quantity is the balance, inventory_movements the ledger."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product
from app.models_orders import InventoryMovement


class InsufficientStock(Exception):
    def __init__(self, product_id: uuid.UUID, available: int, requested: int):
        self.product_id = product_id
        self.available = available
        self.requested = requested
        super().__init__(f"insufficient stock for {product_id}: {available} < {requested}")


def lock_product(db: Session, business_id: uuid.UUID, product_id: uuid.UUID) -> Product | None:
    """SELECT ... FOR UPDATE so two concurrent changes cannot oversell."""
    return db.scalar(
        select(Product)
        .where(Product.business_id == business_id, Product.id == product_id)
        .with_for_update()
    )


def apply_delta(
    db: Session, product: Product, delta: int, reason: str,
    order_id: uuid.UUID | None = None, note: str | None = None,
) -> InventoryMovement:
    """Caller must hold the row lock (lock_product). Refuses to go below zero."""
    new_balance = product.stock_quantity + delta
    if new_balance < 0:
        raise InsufficientStock(product.id, product.stock_quantity, -delta)
    product.stock_quantity = new_balance
    movement = InventoryMovement(
        business_id=product.business_id, product_id=product.id, delta=delta,
        balance_after=new_balance, reason=reason, order_id=order_id, note=note,
    )
    db.add(movement)
    db.flush()
    return movement


def list_movements(
    db: Session, business_id: uuid.UUID, product_id: uuid.UUID
) -> list[InventoryMovement]:
    return list(
        db.scalars(
            select(InventoryMovement)
            .where(
                InventoryMovement.business_id == business_id,
                InventoryMovement.product_id == product_id,
            )
            .order_by(InventoryMovement.id)
        )
    )


def low_stock(db: Session, business_id: uuid.UUID, threshold: int) -> list[Product]:
    return list(
        db.scalars(
            select(Product)
            .where(
                Product.business_id == business_id,
                Product.is_active.is_(True),
                Product.stock_quantity <= threshold,
            )
            .order_by(Product.stock_quantity, Product.sku)
        )
    )
