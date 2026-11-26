"""Milestone 2 models: customers, customer prices, inventory movements, orders.

Every table carries business_id. Child rows reference parents through composite
foreign keys (business_id, id), so the database itself refuses cross-tenant links.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
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
    lines: Mapped[list["OrderLine"]] = relationship(
        back_populates="order", order_by="OrderLine.created_at", lazy="selectin",
        cascade="all, delete-orphan",
    )
    exceptions: Mapped[list["OrderException"]] = relationship(
        back_populates="order", order_by="OrderException.created_at", lazy="selectin",
        cascade="all, delete-orphan",
    )


class OrderLine(Base):
    """Phase 2 (m009): one line of an order, priced from the restaurant menu.

    Evolution of OrderItem: references a menu variant instead of a product and freezes
    names, unit and price at order time. Quantity is fractional (weight-sold items).
    Additive: OrderItem and everything reading it stay untouched until the cutover ends."""

    __tablename__ = "order_lines"
    __table_args__ = (
        UniqueConstraint("order_id", "variant_id", name="uq_order_lines_order_variant"),
        UniqueConstraint("business_id", "id", name="uq_order_lines_business_id"),
        CheckConstraint("quantity > 0", name="ck_line_qty_positive"),
        CheckConstraint(
            "price_source in ('menu','override','cashier_manual')", name="ck_line_price_source"
        ),
        CheckConstraint(
            "price_source = 'cashier_manual' or (priced_by is null and priced_at is null)",
            name="ck_line_rule_price_unattributed",
        ),
        CheckConstraint(
            "price_source <> 'cashier_manual' or (priced_by is not null and priced_at is not null)",
            name="ck_line_manual_price_attributed",
        ),
        ForeignKeyConstraint(
            ["business_id", "order_id"], ["orders.business_id", "orders.id"],
            ondelete="CASCADE", name="fk_line_order_same_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "variant_id"],
            ["menu_item_variants.business_id", "menu_item_variants.id"],
            name="fk_line_variant_same_business",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    variant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    item_name: Mapped[str] = mapped_column(String(300))
    variant_name: Mapped[str] = mapped_column(String(100))
    quantity: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    unit_label: Mapped[str | None] = mapped_column(String(30), nullable=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    price_source: Mapped[str] = mapped_column(String(20))  # menu|override|cashier_manual
    priced_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    priced_at: Mapped[datetime | None] = mapped_column(nullable=True)
    line_total: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    order: Mapped[Order] = relationship(back_populates="lines")


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        UniqueConstraint("order_id", "product_id", name="uq_order_items_order_product"),
        UniqueConstraint("business_id", "id", name="uq_order_items_business_id"),
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


EXCEPTION_STATUSES = ("open", "resolved", "dismissed")
EXCEPTION_REASONS = (
    "price_required", "unclear_item", "special_request", "complaint", "low_confidence", "other",
)


class OrderException(Base):
    """A line of order text the matcher could not safely turn into an item (B3, extended
    in Phase 1). A draft with open exceptions cannot be submitted until each is resolved
    or dismissed. `reason` is the fixed enum; `reason_detail` keeps the original matcher
    code (not_found, duplicate_product, ...)."""

    __tablename__ = "order_exceptions"
    __table_args__ = (
        CheckConstraint(
            "status in ('open','resolved','dismissed')", name="ck_order_exceptions_status"
        ),
        CheckConstraint(
            "reason in ('price_required','unclear_item','special_request','complaint',"
            "'low_confidence','other')",
            name="ck_order_exceptions_reason",
        ),
        CheckConstraint(
            "(status = 'open' and resolved_by is null and resolved_at is null) or "
            "(status <> 'open' and resolved_by is not null and resolved_at is not null)",
            name="ck_order_exceptions_resolution",
        ),
        CheckConstraint(
            "status <> 'dismissed' or length(btrim(resolution_note, ' ')) > 0",
            name="ck_order_exceptions_dismiss_note",
        ),
        ForeignKeyConstraint(
            ["business_id", "order_id"],
            ["orders.business_id", "orders.id"],
            ondelete="CASCADE",
            name="fk_exception_order_same_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "order_line_id"],
            ["order_items.business_id", "order_items.id"],
            ondelete="SET NULL",
            name="fk_exception_line_same_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "resolved_by"],
            ["users.business_id", "users.id"],
            name="fk_exception_resolver_same_business",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    order_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    order_line_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    raw_text: Mapped[str] = mapped_column(String(500))
    reason: Mapped[str] = mapped_column(String(30))
    reason_detail: Mapped[str | None] = mapped_column(String(30), nullable=True)
    customer_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    quantity: Mapped[int | None] = mapped_column(nullable=True)
    candidates: Mapped[list] = mapped_column(JSONB, default=list)
    status: Mapped[str] = mapped_column(String(20), default="open")
    resolution_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    order: Mapped[Order] = relationship(back_populates="exceptions")


class TelegramLinkCode(Base):
    """One-time code the owner hands to a customer. Only its SHA-256 is stored."""

    __tablename__ = "telegram_link_codes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "customer_id"],
            ["customers.business_id", "customers.id"],
            ondelete="CASCADE",
            name="fk_tglinkcode_customer_same_business",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column()
    used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TelegramLink(Base):
    """A Telegram private chat bound to exactly one customer of exactly one business.
    chat_id is unique across the whole system, so a chat can never reach two tenants."""

    __tablename__ = "telegram_links"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "customer_id"],
            ["customers.business_id", "customers.id"],
            ondelete="CASCADE",
            name="fk_tglink_customer_same_business",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class TelegramUpdate(Base):
    """Processed Telegram update ids: a redelivered update never creates a second order."""

    __tablename__ = "telegram_updates"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class ChatSession(Base):
    """One open conversation per Telegram chat: where the order dialogue stands.
    `draft` holds the lines collected so far; nothing here is an order until the customer
    confirms. Tenant and customer come from the telegram link, never from message text."""

    __tablename__ = "chat_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "customer_id"], ["customers.business_id", "customers.id"],
            name="fk_chatsession_customer_same_business", ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    chat_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    state: Mapped[str] = mapped_column(String(20), default="collecting")
    draft: Mapped[dict] = mapped_column(JSONB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())
