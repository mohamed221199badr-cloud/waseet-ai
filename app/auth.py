"""Password hashing (bcrypt) and JWT access tokens (HS256, PyJWT)."""

import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import settings

ALGORITHM = "HS256"
# Used to burn the same time when the email is unknown, so login timing does not
# reveal which accounts exist.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt(rounds=12)).decode()


def hash_password(password: str) -> str:
    # bcrypt only reads the first 72 bytes; refuse longer input instead of truncating silently
    raw = password.encode()
    if len(raw) > 72:
        raise ValueError("password longer than 72 bytes")
    return bcrypt.hashpw(raw, bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, password_hash: str | None) -> bool:
    raw = password.encode()[:72]
    if not password_hash:
        bcrypt.checkpw(raw, _DUMMY_HASH.encode())
        return False
    try:
        return bcrypt.checkpw(raw, password_hash.encode())
    except ValueError:
        return False


def create_access_token(user_id: uuid.UUID, business_id: uuid.UUID) -> tuple[str, int]:
    expires = timedelta(minutes=settings.jwt_expire_minutes)
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "bid": str(business_id), "iat": now, "exp": now + expires}
    return jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM), int(
        expires.total_seconds()
    )


def decode_access_token(token: str) -> dict:
    """Raises jwt.PyJWTError for anything invalid, expired or tampered."""
    return jwt.decode(
        token, settings.jwt_secret, algorithms=[ALGORITHM],
        options={"require": ["exp", "sub", "bid"]},
    )
