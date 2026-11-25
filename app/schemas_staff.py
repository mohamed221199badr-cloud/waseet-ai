"""Staff API schemas (Phase 1, F7)."""

from pydantic import BaseModel, Field

from app.permissions import STAFF_ROLES


class StaffCreate(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    email: str = Field(min_length=3, max_length=320)
    role: str
    password: str = Field(min_length=8, max_length=200)


class StaffUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: str | None = None


class StaffRead(BaseModel):
    id: str
    full_name: str
    email: str
    role: str
    is_active: bool
    must_change_password: bool


def validate_role(role: str) -> str:
    if role not in STAFF_ROLES:
        raise ValueError("unknown role")
    return role
