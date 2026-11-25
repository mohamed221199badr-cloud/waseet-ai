"""Refresh sessions and the DB-backed login rate limit (F3).

Refresh tokens are 256-bit random strings; only their SHA-256 is stored (the token is
high entropy, so a fast hash is correct here - bcrypt is for passwords). Refresh rotates:
each use revokes the presented token and issues a new one in the same family. Presenting
an already-revoked token revokes the whole family (theft signal). Deactivating a user
revokes all their sessions.

The rate limit is a sliding window over the login_attempts table, so two workers behind
one database share the count. Unknown emails and wrong passwords produce the same 401
and are both recorded, so the counter cannot be used to enumerate accounts.
"""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth import create_access_token, hash_password, verify_password
from app.config import settings
from app.models import Business, User
from app.models_roles import LoginAttempt, UserSession

PRUNE_OLDER_THAN = timedelta(hours=24)


class AuthError(Exception):
    def __init__(self, status: int, detail: str, retry_after: int | None = None):
        super().__init__(detail)
        self.status = status
        self.detail = detail
        self.retry_after = retry_after


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_refresh_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def _prune(db: Session) -> None:
    db.execute(delete(LoginAttempt).where(LoginAttempt.at < _now() - PRUNE_OLDER_THAN))


def _failures_in_window(db: Session, *, email_norm: str, ip: str | None) -> tuple[int, int, int]:
    """(failures for the email, failures for the ip, seconds until the oldest relevant
    failure leaves the window) over the sliding window."""
    since = _now() - timedelta(minutes=settings.login_window_minutes)
    email_count = db.scalar(
        select(func.count()).select_from(LoginAttempt).where(
            LoginAttempt.email_norm == email_norm, LoginAttempt.at >= since,
            LoginAttempt.success.is_(False),
        )
    ) or 0
    ip_count = 0
    oldest = None
    if ip:
        ip_count = db.scalar(
            select(func.count()).select_from(LoginAttempt).where(
                LoginAttempt.ip == ip, LoginAttempt.at >= since,
                LoginAttempt.success.is_(False),
            )
        ) or 0
        oldest = db.scalar(
            select(func.min(LoginAttempt.at)).where(
                LoginAttempt.ip == ip, LoginAttempt.at >= since,
                LoginAttempt.success.is_(False),
            )
        )
    oldest_email = db.scalar(
        select(func.min(LoginAttempt.at)).where(
            LoginAttempt.email_norm == email_norm, LoginAttempt.at >= since,
            LoginAttempt.success.is_(False),
        )
    )
    candidates = [t for t in (oldest, oldest_email) if t is not None]
    retry_after = 0
    if candidates:
        window = timedelta(minutes=settings.login_window_minutes)
        retry_after = max(1, int(((min(candidates) + window) - _now()).total_seconds()))
    return email_count, ip_count, retry_after


def _record(db: Session, email_norm: str, ip: str | None, success: bool) -> None:
    db.add(LoginAttempt(email_norm=email_norm, ip=ip, success=success))
    db.flush()


def login(
    db: Session, *, business_slug: str, email: str, password: str,
    ip: str | None, user_agent: str | None,
) -> tuple[User, str, int, str]:
    """Verify credentials and open a session. Raises AuthError (401 or 429)."""
    email_norm = email.strip().lower()
    _prune(db)
    email_failures, ip_failures, retry_after = _failures_in_window(
        db, email_norm=email_norm, ip=ip
    )
    if (email_failures >= settings.login_max_failures_per_email
            or ip_failures >= settings.login_max_failures_per_ip):
        raise AuthError(429, "Too many login attempts; try again later", retry_after)

    user = db.scalar(
        select(User).join(Business, Business.id == User.business_id).where(
            Business.slug == business_slug, func.lower(User.email) == email_norm
        )
    )
    ok = verify_password(password, user.password_hash if user else None)
    if user is None or not ok or not user.is_active:
        _record(db, email_norm, ip, success=False)
        db.commit()  # the attempt is a write even when the login itself failed
        # same answer for unknown business, unknown email, wrong password, inactive user
        raise AuthError(401, "Invalid credentials")

    _record(db, email_norm, ip, success=True)
    access_token, ttl = create_access_token(user.id, user.business_id)
    refresh_token = new_refresh_token()
    db.add(
        UserSession(
            business_id=user.business_id, user_id=user.id, family_id=uuid.uuid4(),
            refresh_token_hash=_hash_token(refresh_token),
            expires_at=_now() + timedelta(days=settings.refresh_token_expire_days),
            ip=ip, user_agent=(user_agent or "")[:300] or None,
        )
    )
    db.commit()
    return user, access_token, ttl, refresh_token


def refresh(
    db: Session, *, token: str, ip: str | None, user_agent: str | None
) -> tuple[str, int, str]:
    """Rotate a refresh token: new access + new refresh, the presented one is revoked.
    A revoked or unknown token is a theft signal: the whole family is revoked."""
    row = db.scalar(
        select(UserSession).where(UserSession.refresh_token_hash == _hash_token(token))
    )
    if row is None:
        raise AuthError(401, "Invalid refresh token")
    if row.revoked_at is not None:
        _revoke_family(db, row.family_id)
        db.commit()
        raise AuthError(401, "Invalid refresh token")
    if row.expires_at <= _now():
        raise AuthError(401, "Refresh token expired")
    user = db.scalar(
        select(User).where(User.id == row.user_id, User.business_id == row.business_id)
    )
    if user is None or not user.is_active:
        _revoke_family(db, row.family_id)
        db.commit()
        raise AuthError(401, "Invalid refresh token")

    row.revoked_at = _now()
    row.last_used_at = _now()
    access_token, ttl = create_access_token(user.id, user.business_id)
    new_token = new_refresh_token()
    db.add(
        UserSession(
            business_id=row.business_id, user_id=row.user_id, family_id=row.family_id,
            refresh_token_hash=_hash_token(new_token), expires_at=row.expires_at,
            ip=ip, user_agent=(user_agent or "")[:300] or None,
        )
    )
    db.commit()
    return access_token, ttl, new_token


def _revoke_family(db: Session, family_id: uuid.UUID) -> None:
    for row in db.scalars(
        select(UserSession).where(
            UserSession.family_id == family_id, UserSession.revoked_at.is_(None)
        )
    ):
        row.revoked_at = _now()


def logout(db: Session, *, token: str) -> None:
    """Revoke the presented refresh token. Idempotent: unknown tokens are ignored."""
    row = db.scalar(
        select(UserSession).where(UserSession.refresh_token_hash == _hash_token(token))
    )
    if row is not None and row.revoked_at is None:
        row.revoked_at = _now()
    db.commit()


def revoke_user_sessions(db: Session, business_id: uuid.UUID, user_id: uuid.UUID) -> int:
    """Every live session of a user (deactivation, password change). Returns the count."""
    rows = db.scalars(
        select(UserSession).where(
            UserSession.business_id == business_id, UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
        )
    ).all()
    for row in rows:
        row.revoked_at = _now()
    db.flush()
    return len(rows)


def change_password(
    db: Session, *, user: User, current_password: str, new_password: str
) -> None:
    """The first-login forced change and the ordinary self-service change. All existing
    sessions are revoked so a stolen refresh token dies with the old password."""
    if not verify_password(current_password, user.password_hash):
        raise AuthError(401, "Invalid credentials")
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    revoke_user_sessions(db, user.business_id, user.id)
    db.commit()
