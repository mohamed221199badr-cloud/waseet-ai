"""Shared API dependencies: DB session + tenant resolution.

Milestone 1 has no auth yet; the tenant comes from the X-Business-ID header.
When JWT auth lands (Milestone 2) this dependency is the single place to swap.
"""

import uuid

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Business


def get_business_id(
    x_business_id: str = Header(..., description="UUID of the business (tenant)"),
) -> uuid.UUID:
    try:
        return uuid.UUID(x_business_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid X-Business-ID header") from exc


def get_business(
    business_id: uuid.UUID = Depends(get_business_id),
    db: Session = Depends(get_db),
) -> Business:
    business = db.scalar(select(Business).where(Business.id == business_id))
    if business is None:
        raise HTTPException(status_code=404, detail="Business not found")
    return business
