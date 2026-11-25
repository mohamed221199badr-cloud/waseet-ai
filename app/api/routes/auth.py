"""Login. One owner per business in the MVP; the token identifies user and business."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.auth import create_access_token, verify_password
from app.db import get_db
from app.models import Business, User

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    business_slug: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class MeOut(BaseModel):
    user_id: str
    business_id: str
    business_name: str
    email: str
    role: str


@router.post("/login", response_model=TokenOut)
def login(body: LoginBody, db: Session = Depends(get_db)):
    email = body.email.strip().lower()
    user = db.scalar(
        select(User).join(Business, Business.id == User.business_id).where(
            Business.slug == body.business_slug, func.lower(User.email) == email
        )
    )
    ok = verify_password(body.password, user.password_hash if user else None)
    if user is None or not ok:
        # same answer for unknown business, unknown email and wrong password
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token, ttl = create_access_token(user.id, user.business_id)
    return TokenOut(access_token=token, expires_in=ttl)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    business = db.get(Business, user.business_id)
    return MeOut(
        user_id=str(user.id), business_id=str(user.business_id), business_name=business.name,
        email=user.email, role=user.role,
    )
