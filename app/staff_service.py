"""Staff management (Phase 1, F7). The only place the staff rules live; the routes
and screens call this service.

- Owner (STAFF_MANAGE): full control over every role.
- Manager (STAFF_MANAGE_LIMITED): creates, edits and deactivates only cashier,
  kitchen, storekeeper and delivery. Never an owner or another manager, never
  himself, never a role change to owner or manager.
- A new account gets a temporary password chosen by the creator (never in chat) and
  must_change_password=True; the first login forces a change (F3).
- Staff are deactivated, never deleted; deactivation revokes their sessions.
- Every action writes audit_events with actor, target and old/new values.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.auth import hash_password
from app.auth_sessions import revoke_user_sessions
from app.models import User
from app.permissions import LIMITED_STAFF_ROLES, Permission, role_has


class StaffError(Exception):
    """A staff rule was violated (403/409 in the API)."""

    def __init__(self, detail: str, status: int = 403):
        super().__init__(detail)
        self.detail = detail
        self.status = status


def _check_target(actor: User, target: User | None, new_role: str | None) -> None:
    """F7 scope: what the actor may do to target (None when creating)."""
    if role_has(actor.role, Permission.STAFF_MANAGE):
        return  # owner: no limits
    if not role_has(actor.role, Permission.STAFF_MANAGE_LIMITED):
        raise StaffError("لا تملك صلاحية إدارة الموظفين")
    if target is not None:
        if target.id == actor.id:
            raise StaffError("المدير لا يستطيع تعديل حسابه بنفسه")
        if target.role not in LIMITED_STAFF_ROLES:
            raise StaffError("المدير لا يستطيع تعديل حساب مدير أو مالك")
    if new_role is not None and new_role not in LIMITED_STAFF_ROLES:
        raise StaffError("المدير لا يستطيع إنشاء أو تعيين دور مدير أو مالك")


def list_staff(db: Session, business_id: uuid.UUID) -> list[User]:
    return list(db.scalars(select(User).where(
        User.business_id == business_id, User.role != "system")
        .order_by(User.created_at)))


def create_staff(db: Session, actor: User, *, full_name: str, email: str,
                 role: str, password: str) -> User:
    _check_target(actor, None, role)
    email = email.strip().lower()
    if db.scalar(select(User).where(User.business_id == actor.business_id,
                                    User.email == email)):
        raise StaffError("الإيميل مستخدم بالفعل", status=409)
    user = User(business_id=actor.business_id, full_name=full_name, email=email,
                role=role, password_hash=hash_password(password),
                must_change_password=True)
    db.add(user)
    db.flush()
    write_audit(db, business_id=actor.business_id, actor_id=actor.id, entity="user",
                entity_id=str(user.id), action="create",
                payload={"email": email, "role": role})
    return user


def update_staff(db: Session, actor: User, target: User, *,
                 full_name: str | None = None, role: str | None = None) -> User:
    _check_target(actor, target, role)
    old = {"full_name": target.full_name, "role": target.role}
    if full_name is not None:
        target.full_name = full_name
    if role is not None:
        target.role = role
    write_audit(db, business_id=actor.business_id, actor_id=actor.id, entity="user",
                entity_id=str(target.id), action="update",
                payload={"old": old,
                         "new": {"full_name": target.full_name, "role": target.role}})
    return target


def deactivate_staff(db: Session, actor: User, target: User) -> User:
    _check_target(actor, target, None)
    target.is_active = False
    revoked = revoke_user_sessions(db, actor.business_id, target.id)
    write_audit(db, business_id=actor.business_id, actor_id=actor.id, entity="user",
                entity_id=str(target.id), action="update",
                payload={"old": {"is_active": True}, "new": {"is_active": False},
                         "sessions_revoked": revoked})
    return target
