"""Phase 1 (m007) models: the restaurant menu. Data-entry only in Phase 1 (B2):
the chat bot, orders and inventory keep reading `products` until the Phase 2 cutover.

Tenancy pattern matches the rest of the schema: every table carries business_id and
children reference parents through composite foreign keys (business_id, id).
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base

MENU_ITEM_KINDS = ("food", "service")
SOLD_BY = ("unit", "weight")
OVERRIDE_SCOPES = ("customer", "segment")


class MenuItem(Base):
    """One sellable thing (a dish, a cut of meat, a service). The natural key is
    (business_id, category, name): the same name may live in two categories on purpose."""

    __tablename__ = "menu_items"
    __table_args__ = (
        UniqueConstraint("business_id", "id", name="uq_menu_items_business_id"),
        UniqueConstraint("business_id", "category", "name", name="uq_menu_items_natural"),
        CheckConstraint("kind in ('food','service')", name="ck_menu_items_kind"),
        CheckConstraint("sold_by in ('unit','weight')", name="ck_menu_items_sold_by"),
        CheckConstraint(
            "sold_by <> 'weight' or unit_label is not null", name="ck_menu_items_weight_label"
        ),
        CheckConstraint("min_quantity is null or min_quantity > 0", name="ck_menu_items_min_qty"),
        ForeignKeyConstraint(
            ["business_id", "category"], ["menu_categories.business_id", "menu_categories.name"],
            name="fk_menu_item_category_same_business",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    category: Mapped[str] = mapped_column(String(200))
    name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(10), default="food")
    sold_by: Mapped[str] = mapped_column(String(10))
    unit_label: Mapped[str | None] = mapped_column(String(30), nullable=True)  # e.g. "كيلو"
    min_quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    sort_order: Mapped[int] = mapped_column(default=0)
    is_available: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    variants: Mapped[list["MenuItemVariant"]] = relationship(
        back_populates="item", order_by="MenuItemVariant.sort_order", lazy="selectin",
        cascade="all, delete-orphan",
    )
    aliases: Mapped[list["MenuItemAlias"]] = relationship(
        back_populates="item", lazy="selectin", cascade="all, delete-orphan"
    )


class MenuCategory(Base):
    """A menu section (اللحوم, الفراخ, ...). Named rows so categories are tenant-scoped
    and reusable; an item's category always points at one of them."""

    __tablename__ = "menu_categories"
    __table_args__ = (
        UniqueConstraint("business_id", "name", name="uq_menu_categories_name"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class MenuItemVariant(Base):
    """A priced option of an item (size, cut, grade). price NULL means "ask the cashier":
    the bot never quotes it (B4). Exactly one variant per item is the default."""

    __tablename__ = "menu_item_variants"
    __table_args__ = (
        UniqueConstraint("business_id", "id", name="uq_variants_business_id"),
        UniqueConstraint("business_id", "item_id", "name", name="uq_variants_natural"),
        CheckConstraint("price is null or price >= 0", name="ck_variant_price_nonneg"),
        ForeignKeyConstraint(
            ["business_id", "item_id"], ["menu_items.business_id", "menu_items.id"],
            ondelete="CASCADE", name="fk_variant_item_same_business",
        ),
        # One default variant per item: enforced by the partial unique index created
        # in m007 (PostgreSQL cannot express it as a table constraint).
        Index(
            "uq_variant_one_default", "business_id", "item_id",
            unique=True, postgresql_where=text("is_default"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    name: Mapped[str] = mapped_column(String(100))
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    is_default: Mapped[bool] = mapped_column(default=False)
    sort_order: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    item: Mapped[MenuItem] = relationship(back_populates="variants")


class MenuItemAlias(Base):
    """Alternative names customers use for a menu item (letter folding lives in the
    matcher, not in identity: two different dishes are never merged)."""

    __tablename__ = "menu_item_aliases"
    __table_args__ = (
        UniqueConstraint("business_id", "alias", name="uq_menu_aliases_business_alias"),
        ForeignKeyConstraint(
            ["business_id", "item_id"], ["menu_items.business_id", "menu_items.id"],
            ondelete="CASCADE", name="fk_menualias_item_same_business",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    alias: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    item: Mapped[MenuItem] = relationship(back_populates="aliases")


class PriceOverride(Base):
    """A concrete price that wins over the variant base price (B4): customer-specific or
    segment-wide, optionally time-boxed. price is NOT NULL, so an override can never
    itself be "unknown"."""

    __tablename__ = "price_overrides"
    __table_args__ = (
        CheckConstraint("scope in ('customer','segment')", name="ck_override_scope"),
        CheckConstraint("price >= 0", name="ck_override_price_nonneg"),
        CheckConstraint(
            "(customer_id is null) <> (segment is null)", name="ck_override_one_target"
        ),
        ForeignKeyConstraint(
            ["business_id", "variant_id"],
            ["menu_item_variants.business_id", "menu_item_variants.id"],
            ondelete="CASCADE", name="fk_override_variant_same_business",
        ),
        ForeignKeyConstraint(
            ["business_id", "customer_id"], ["customers.business_id", "customers.id"],
            ondelete="CASCADE", name="fk_override_customer_same_business",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    variant_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    scope: Mapped[str] = mapped_column(String(10))
    customer_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    segment: Mapped[str | None] = mapped_column(String(100), nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    starts_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ends_at: Mapped[datetime | None] = mapped_column(nullable=True)
    is_active: Mapped[bool] = mapped_column(default=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class RestaurantSettings(Base):
    """Per-business tunables (one row per business). Thresholds from B5/F5 live here,
    not in code: re-confirmation triggers and the COD refund limit."""

    __tablename__ = "restaurant_settings"

    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), primary_key=True
    )
    reconfirm_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), default=10)
    reconfirm_abs_egp: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=50)
    refund_limit_egp: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=100)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())


class MenuItemImage(Base):
    """One photo per menu item (m013): bytes plus the cached Telegram file_id so
    the bot uploads once and then resends by reference."""

    __tablename__ = "menu_item_images"
    __table_args__ = (
        UniqueConstraint("business_id", "item_id", name="uq_item_image"),
        ForeignKeyConstraint(
            ["business_id", "item_id"], ["menu_items.business_id", "menu_items.id"],
            ondelete="CASCADE", name="fk_item_image_same_business"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    item_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    image: Mapped[bytes] = mapped_column(LargeBinary)
    content_type: Mapped[str] = mapped_column(String(50))
    telegram_file_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
