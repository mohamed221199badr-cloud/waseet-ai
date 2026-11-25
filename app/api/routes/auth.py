"""Login, refresh, logout, password change. Sessions and the rate limit live in
app/auth_sessions.py (F3); the rate limit is DB-backed so workers share the count."""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app import auth_sessions
from app.api.deps import get_current_user
from app.auth_sessions import AuthError
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
    refresh_token: str


class RefreshBody(BaseModel):
    refresh_token: str = Field(min_length=10, max_length=200)


class ChangePasswordBody(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=8, max_length=200)


class MeOut(BaseModel):
    user_id: str
    business_id: str
    business_name: str
    email: str
    role: str
    must_change_password: bool


def _raise(exc: AuthError) -> None:
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after else None
    raise HTTPException(status_code=exc.status, detail=exc.detail, headers=headers)


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/login", response_model=TokenOut)
def login(body: LoginBody, request: Request, db: Session = Depends(get_db)):
    try:
        user, access_token, ttl, refresh_token = auth_sessions.login(
            db, business_slug=body.business_slug, email=body.email,
            password=body.password, ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except AuthError as exc:
        _raise(exc)
    return TokenOut(access_token=access_token, expires_in=ttl, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenOut)
def refresh(body: RefreshBody, request: Request, db: Session = Depends(get_db)):
    try:
        access_token, ttl, refresh_token = auth_sessions.refresh(
            db, token=body.refresh_token, ip=_client_ip(request),
            user_agent=request.headers.get("user-agent"),
        )
    except AuthError as exc:
        _raise(exc)
    return TokenOut(access_token=access_token, expires_in=ttl, refresh_token=refresh_token)


@router.post("/logout", status_code=204)
def logout(body: RefreshBody, db: Session = Depends(get_db)):
    auth_sessions.logout(db, token=body.refresh_token)
    return Response(status_code=204)


@router.post("/change-password", status_code=204)
def change_password(
    body: ChangePasswordBody,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        auth_sessions.change_password(
            db, user=user, current_password=body.current_password,
            new_password=body.new_password,
        )
    except AuthError as exc:
        _raise(exc)
    return Response(status_code=204)


@router.get("/me", response_model=MeOut)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    business = db.get(Business, user.business_id)
    return MeOut(
        user_id=str(user.id), business_id=str(user.business_id), business_name=business.name,
        email=user.email, role=user.role, must_change_password=user.must_change_password,
    )
