"""Test helpers: JWT auth headers for a synthetic business."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import create_access_token
from app.models import User
from tests.conftest import TEST_URL


def auth_headers(business_id: uuid.UUID) -> dict:
    """Bearer header for the owner of `business_id`. An unknown business gets a token for a
    user that does not exist (so the API must answer 401)."""
    from sqlalchemy import create_engine

    eng = create_engine(TEST_URL)
    with Session(eng) as s:
        user_id = s.scalar(select(User.id).where(User.business_id == business_id))
    eng.dispose()
    token, _ = create_access_token(user_id or uuid.uuid4(), business_id)
    return {"Authorization": f"Bearer {token}"}
