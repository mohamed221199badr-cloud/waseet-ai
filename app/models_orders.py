"""Milestone 2 models: customers, customer prices, inventory movements, orders.

Every table carries business_id. Child rows reference parents through composite
foreign keys (business_id, id), so the database itself refuses cross-tenant links.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

ORDER_STATUSES = ("draft", "pending", "confirmed", "rejected")
ORDER_CHANNELS = ("manual", "whatsapp", "telegram", "web")
MOVEMENT_REASONS = ("restock", "adjustment", "order_confirmed")


class Customer(Base):
    __tablename__ = "customers"
    __table_args__ = (
        UniqueConstraint("business_id", "id", name="uq_customers_business_id"),
        UniqueConstraint("business_id", "phone", name="uq_customers_business_phone"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class CustomerPrice(Base):
    """Special price of one product for one customer. Overrides products.price."""

    __tablename__ = "customer_prices"
    __table_args__ = (
        UniqueConstraint("business_id", "customer_id", "product_id", name="uq_cprice_unique"),
        CheckConstraint("price >= 0", name="ck_cprice_nonneg"),
        ForeignKeyConstraint(
            ["business_id", "customer_id"],
            ["customers.business_id", "customers.id"],
            ondelete="CASCADE",
            name="fk_cprice_customer_same_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "product_id"],
            ["products.business_id", "products.id"],
            ondelete="CASCADE",
            name="fk_cprice_product_same_business",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class InventoryMovement(Base):
    """Append-only stock ledger. products.stock_quantity is the running balance."""

    __tablename__ = "inventory_movements"
    __table_args__ = (
        CheckConstraint("delta <> 0", name="ck_movement_nonzero"),
        ForeignKeyConstraint(
            ["business_id", "product_id"],
            ["products.business_id", "products.id"],
            ondelete="CASCADE",
            name="fk_movement_product_same_business",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    delta: Mapped[int]
    balance_after: Mapped[int]
    reason: Mapped[str] = mapped_column(String(30))
    order_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    note: Mapped[str | None] = mapped_column(String(300), nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("business_id", "id", name="uq_orders_business_id"),
        CheckConstraint(
            "status in ('draft','pending','confirmed','rejected')", name="ck_orders_status"
        ),
        CheckConstraint(
            "channel in ('manual','whatsapp','telegram','web')", name="ck_orders_channel"
        ),
        ForeignKeyConstraint(
            ["business_id", "customer_id"],
            ["customers.business_id", "customers.id"],
            name="fk_order_customer_same_business",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # Channel-independent intake: where the order text came from, never how it is processed.
    channel: Mapped[str] = mapped_column(String(20), default="manual")
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    total: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(nullable=True)

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", order_by="OrderItem.created_at", lazy="selectin",
        cascade="all, delete-orphan",
    )


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "product_id", name="uq_order_items_order_product"),
        CheckConstraint("quantity > 0", name="ck_item_qty_positive"),
        ForeignKeyConstraint(
            ["business_id", "order_id"],
            ["orders.business_id", "orders.id"],
            ondelete="CASCADE",
            name="fk_item_order_same_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "product_id"],
            ["products.business_id", "products.id"],
            name="fk_item_product_same_business",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    quantity: Mapped[int]
    # Price is frozen when the item is added: later price changes never rewrite an order.
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    price_source: Mapped[str] = mapped_column(String(20), default="catalog")  # catalog|customer
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    order: Mapped[Order] = relationship(back_populates="items")
