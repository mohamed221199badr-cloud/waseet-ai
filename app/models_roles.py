"""Phase 1 (m006) models: staff sessions, login attempt log, staff notification channels.

Every table carries business_id. Child rows reference parents through composite
foreign keys (business_id, id), so the database itself refuses cross-tenant links.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.permissions import ALL_ROLES, STAFF_ROLES

_role_check = "role in (" + ",".join(f"'{r}'" for r in ALL_ROLES) + ")"
_staff_role_check = "role in (" + ",".join(f"'{r}'" for r in STAFF_ROLES) + ")"


def system_user_id(business_id: uuid.UUID) -> uuid.UUID:
    """Deterministic id of the internal system actor of a business (proposal v3, C2):
    code and tests find it without a lookup, and it is stable across environments."""
    return uuid.uuid5(business_id, "system")


class UserSession(Base):
    """One refresh-token family member (F3). Only the SHA-256 of the refresh token is
    stored; the raw token is never stored or logged. Refresh rotates: a new row is
    added and this row is revoked. Presenting an already-revoked token revokes the
    whole family (theft signal)."""

    __tablename__ = "user_sessions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["business_id", "user_id"], ["users.business_id", "users.id"],
            ondelete="CASCADE", name="fk_session_user_same_business",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    refresh_token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    issued_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column()
    last_used_at: Mapped[datetime | None] = mapped_column(nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(300), nullable=True)


class LoginAttempt(Base):
    """DB-backed login rate limiting (F3). Both unknown emails and wrong passwords are
    recorded, so the counter cannot be used to tell which accounts exist."""

    __tablename__ = "login_attempts"
    __table_args__ = (
        Index("ix_login_attempts_email_at", "email_norm", "at"),
        Index("ix_login_attempts_ip_at", "ip", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email_norm: Mapped[str] = mapped_column(String(320))
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    at: Mapped[datetime] = mapped_column(server_default=func.now())
    success: Mapped[bool] = mapped_column(default=False)


class StaffChannel(Base):
    """Where staff notifications go (F2). A `role` row is one Telegram group for that
    role; a `user_id` row is an individual DM. Exactly one of the two is set."""

    __tablename__ = "staff_channels"
    __table_args__ = (
        CheckConstraint(_staff_role_check, name="ck_staff_channels_role"),
        CheckConstraint("channel in ('telegram')", name="ck_staff_channels_channel"),
        CheckConstraint("(role is null) <> (user_id is null)", name="ck_staff_channels_one_target"),
        UniqueConstraint("business_id", "role", "channel", name="uq_staff_channels_role"),
        UniqueConstraint("business_id", "user_id", "channel", name="uq_staff_channels_user"),
        ForeignKeyConstraint(
            ["business_id", "user_id"], ["users.business_id", "users.id"],
            ondelete="CASCADE", name="fk_staffchannel_user_same_business",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    business_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str | None] = mapped_column(String(20), nullable=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    channel: Mapped[str] = mapped_column(String(20), default="telegram")
    address: Mapped[str] = mapped_column(String(64))  # Telegram chat id (group or user)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now(), onupdate=func.now())
