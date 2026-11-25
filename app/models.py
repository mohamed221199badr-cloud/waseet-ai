"""SQLAlchemy models. Every business table carries business_id (multi-tenant)."""

import uuid
from datetime import datetime

from sqlalchemy import (
    ForeignKey,
    ForeignKeyConstraint,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Business(Base):
    __tablename__ = "businesses"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(200))
    slug: Mapped[str] = mapped_column(String(100), unique=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    users: Mapped[list["User"]] = relationship(back_populates="business")
    products: Mapped[list["Product"]] = relationship(back_populates="business")


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("business_id", "email", name="uq_users_business_email"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    full_name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320))
    # Milestone 1: single owner per business. Roles (owner/approver/staff) arrive with auth later.
    role: Mapped[str] = mapped_column(String(20), default="owner")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    business: Mapped[Business] = relationship(back_populates="users")


class Product(Base):
    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("business_id", "sku", name="uq_products_business_sku"),
        # Composite uniqueness lets tenant-scoped composite FKs point at (business_id, id).
        UniqueConstraint("business_id", "id", name="uq_products_business_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(300))
    unit: Mapped[str] = mapped_column(String(30), default="piece")
    price: Mapped[float] = mapped_column(Numeric(12, 2))
    stock_quantity: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())

    business: Mapped[Business] = relationship(back_populates="products")
    aliases: Mapped[list["ProductAlias"]] = relationship(back_populates="product")


class ProductAlias(Base):
    """Colloquial/alternate names customers use for a product (e.g. Egyptian Arabic)."""

    __tablename__ = "product_aliases"
    __table_args__ = (
        UniqueConstraint("business_id", "alias", name="uq_aliases_business_alias"),
        # DB-level tenancy: an alias can only reference a product of the SAME business.
        ForeignKeyConstraint(
            ["business_id", "product_id"],
            ["products.business_id", "products.id"],
            ondelete="CASCADE",
            name="fk_alias_product_same_business",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    product_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    alias: Mapped[str] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    product: Mapped[Product] = relationship(back_populates="aliases")


class AuditEvent(Base):
    """Append-only audit trail for every write."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    entity: Mapped[str] = mapped_column(String(50))
    entity_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(20))  # create | update | delete
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


# Milestone 2 tables (registered on the same Base)
from app import models_orders  # noqa: E402,F401
