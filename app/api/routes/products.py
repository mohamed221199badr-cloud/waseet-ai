"""Tenant-scoped Products API. All routes require X-Business-ID."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_business, require
from app.audit import write_audit
from app.db import get_db
from app.models import Business
from app.permissions import Permission as P
from app.repositories import products as repo
from app.schemas import AliasCreate, AliasRead, ProductCreate, ProductRead, ProductUpdate

router = APIRouter(prefix="/products", tags=["products"])


@router.get("", response_model=list[ProductRead])
def list_products(
    is_active: bool | None = None,
    _: None = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
) -> list:
    return repo.list_products(db, business.id, is_active)


@router.post("", response_model=ProductRead, status_code=status.HTTP_201_CREATED)
def create_product(
    body: ProductCreate,
    _: None = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    try:
        product = repo.create_product(db, business.id, body.model_dump())
        write_audit(
            db,
            business_id=business.id,
            entity="product",
            entity_id=str(product.id),
            action="create",
            payload=body.model_dump(mode="json"),
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="A product with this SKU already exists in this business"
        ) from None
    return product


def _get_or_404(db: Session, business_id: uuid.UUID, product_id: uuid.UUID):
    product = repo.get_product(db, business_id, product_id)
    if product is None:
        # Same 404 whether the product does not exist or belongs to another tenant:
        # we never reveal cross-tenant existence.
        raise HTTPException(status_code=404, detail="Product not found")
    return product


@router.get("/{product_id}", response_model=ProductRead)
def get_product(
    product_id: uuid.UUID,
    _: None = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    return _get_or_404(db, business.id, product_id)


@router.patch("/{product_id}", response_model=ProductRead)
def update_product(
    product_id: uuid.UUID,
    body: ProductUpdate,
    _: None = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    product = _get_or_404(db, business.id, product_id)
    changes = body.model_dump(exclude_unset=True, mode="json")
    repo.update_product(product, changes)
    write_audit(
        db,
        business_id=business.id,
        entity="product",
        entity_id=str(product.id),
        action="update",
        payload=changes,
    )
    db.commit()
    return product


@router.delete("/{product_id}", response_model=ProductRead)
def deactivate_product(
    product_id: uuid.UUID,
    _: None = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    """Products are never deleted (order lines and stock history point at them).
    DELETE deactivates: is_active=false, hidden from matching and new orders.
    Re-activate with PATCH {"is_active": true}. Idempotent."""
    product = _get_or_404(db, business.id, product_id)
    if product.is_active:
        repo.update_product(product, {"is_active": False})
        write_audit(
            db,
            business_id=business.id,
            entity="product",
            entity_id=str(product.id),
            action="update",
            payload={"is_active": False, "via": "DELETE"},
        )
        db.commit()
    return product


@router.get("/{product_id}/aliases", response_model=list[AliasRead])
def list_aliases(
    product_id: uuid.UUID,
    _: None = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    _get_or_404(db, business.id, product_id)
    return repo.list_aliases(db, business.id, product_id)


@router.post("/{product_id}/aliases", response_model=AliasRead, status_code=status.HTTP_201_CREATED)
def create_alias(
    product_id: uuid.UUID,
    body: AliasCreate,
    _: None = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    _get_or_404(db, business.id, product_id)
    try:
        alias = repo.create_alias(db, business.id, product_id, body.alias)
        write_audit(
            db,
            business_id=business.id,
            entity="product_alias",
            entity_id=str(alias.id),
            action="create",
            payload={"alias": body.alias, "product_id": str(product_id)},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="This alias already exists in this business"
        ) from None
    return alias
