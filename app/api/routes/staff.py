"""Staff routes (Phase 1, F7): all rules live in app.staff_service."""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import staff_service
from app.api.deps import get_db, require_any
from app.models import User
from app.permissions import Permission
from app.schemas_staff import StaffCreate, StaffRead, StaffUpdate, validate_role

router = APIRouter(prefix="/staff", tags=["staff"])


def _read(user: User) -> StaffRead:
    return StaffRead(id=str(user.id), full_name=user.full_name, email=user.email,
                     role=user.role, is_active=user.is_active,
                     must_change_password=user.must_change_password)


@router.get("", response_model=list[StaffRead])
def list_staff(user: User = Depends(require_any(Permission.STAFF_MANAGE,
                                        Permission.STAFF_MANAGE_LIMITED)),
               db: Session = Depends(get_db)):
    return [_read(u) for u in staff_service.list_staff(db, user.business_id)]


@router.post("", response_model=StaffRead, status_code=201)
def create_staff(body: StaffCreate,
                 user: User = Depends(require_any(Permission.STAFF_MANAGE,
                                        Permission.STAFF_MANAGE_LIMITED)),
                 db: Session = Depends(get_db)):
    try:
        validate_role(body.role)
        new = staff_service.create_staff(db, user, full_name=body.full_name,
                                         email=body.email, role=body.role,
                                         password=body.password)
        db.commit()
        return _read(new)
    except ValueError:
        raise HTTPException(status_code=422, detail="unknown role") from None
    except staff_service.StaffError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None


def _get(db: Session, user: User, staff_id: uuid.UUID) -> User:
    target = db.get(User, staff_id)
    if target is None or target.business_id != user.business_id:
        raise HTTPException(status_code=404, detail="staff member not found")
    return target


@router.patch("/{staff_id}", response_model=StaffRead)
def update_staff(staff_id: uuid.UUID, body: StaffUpdate,
                 user: User = Depends(require_any(Permission.STAFF_MANAGE,
                                        Permission.STAFF_MANAGE_LIMITED)),
                 db: Session = Depends(get_db)):
    target = _get(db, user, staff_id)
    try:
        if body.role is not None:
            validate_role(body.role)
        staff_service.update_staff(db, user, target, full_name=body.full_name,
                                   role=body.role)
        db.commit()
        return _read(target)
    except ValueError:
        db.rollback()
        raise HTTPException(status_code=422, detail="unknown role") from None
    except staff_service.StaffError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None


@router.post("/{staff_id}/deactivate", response_model=StaffRead)
def deactivate_staff(staff_id: uuid.UUID,
                     user: User = Depends(require_any(Permission.STAFF_MANAGE,
                                              Permission.STAFF_MANAGE_LIMITED)),
                     db: Session = Depends(get_db)):
    target = _get(db, user, staff_id)
    try:
        staff_service.deactivate_staff(db, user, target)
        db.commit()
        return _read(target)
    except staff_service.StaffError as exc:
        db.rollback()
        raise HTTPException(status_code=exc.status, detail=exc.detail) from None
