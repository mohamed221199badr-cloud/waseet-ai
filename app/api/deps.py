"""Shared API dependencies: DB session, authenticated user, tenant.

Milestone 3: the tenant comes from the JWT (Authorization: Bearer ...), never from a
client-supplied header. The token carries user id + business id; both are re-checked
against the database on every request, so a deleted user's token stops working.
"""

import uuid

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import decode_access_token
from app.db import get_db
from app.models import Business, User
from app.permissions import Permission, permissions_for_role

_bearer = HTTPBearer(auto_error=False)


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=401, detail="Invalid or missing token",
        headers={"WWW-Authenticate": "Bearer"},
    )


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    if creds is None:
        raise _unauthorized()
    try:
        claims = decode_access_token(creds.credentials)
        user_id, business_id = uuid.UUID(claims["sub"]), uuid.UUID(claims["bid"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise _unauthorized() from None
    user = db.scalar(select(User).where(User.id == user_id, User.business_id == business_id))
    if user is None or not user.password_hash or not user.is_active:
        raise _unauthorized()
    return user


def get_business(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Business:
    business = db.scalar(select(Business).where(Business.id == user.business_id))
    if business is None:
        raise _unauthorized()
    return business


def require(*perms: Permission):
    """Route guard: the caller's role must hold every listed permission. The map from
    roles to permissions lives in app/permissions.py; this is the only enforcement point
    for routes. Services add their own checks where the rule is data-dependent."""

    def checker(user: User = Depends(get_current_user)) -> User:
        held = permissions_for_role(user.role)
        missing = [p for p in perms if p not in held]
        if missing:
            raise HTTPException(status_code=403, detail="Missing permission")
        return user

    return checker


def require_any(*perms: Permission):
    """Route guard: the caller's role must hold at least one of the listed permissions
    (e.g. staff routes open to STAFF_MANAGE or STAFF_MANAGE_LIMITED)."""

    def checker(user: User = Depends(get_current_user)) -> User:
        held = permissions_for_role(user.role)
        if not any(p in held for p in perms):
            raise HTTPException(status_code=403, detail="Missing permission")
        return user

    return checker
