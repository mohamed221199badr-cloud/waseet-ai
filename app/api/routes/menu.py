"""Menu management API (Phase 1). Data-entry only: the bot keeps reading `products`
until the Phase 2 cutover (B2), so nothing here changes what customers are quoted.

Permissions (F1): MENU_MANAGE for structure, MENU_TOGGLE_AVAILABILITY for the kitchen's
sold-out switch, PRICE_MANAGE for changing the price of an existing variant.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_business, require
from app.audit import write_audit
from app.db import get_db
from app.models import Business, User
from app.permissions import Permission as P
from app.repositories import menu as repo
from app.schemas_menu import (
    AliasIn,
    AliasRead,
    AvailabilityIn,
    CategoryCreate,
    CategoryRead,
    ItemCreate,
    ItemRead,
    ItemUpdate,
    VariantIn,
    VariantRead,
    VariantUpdate,
)

router = APIRouter(prefix="/menu", tags=["menu"])


def _item_or_404(db: Session, business_id: uuid.UUID, item_id: uuid.UUID):
    item = repo.get_item(db, business_id, item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Menu item not found")
    return item


def _menu_error(exc: repo.MenuError) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.get("/categories", response_model=list[CategoryRead])
def list_categories(
    _: User = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    return repo.list_categories(db, business.id)


@router.post("/categories", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
def create_category(
    body: CategoryCreate,
    user: User = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    try:
        row = repo.ensure_category(db, business.id, body.name.strip(), body.sort_order)
        write_audit(db, business_id=business.id, actor_id=user.id, entity="menu_category",
                    entity_id=str(row.id), action="create", payload=body.model_dump(mode="json"))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Category already exists") from None
    return row


@router.get("/items", response_model=list[ItemRead])
def list_items(
    category: str | None = None,
    _: User = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    return repo.list_items(db, business.id, category)


@router.post("/items", response_model=ItemRead, status_code=status.HTTP_201_CREATED)
def create_item(
    body: ItemCreate,
    user: User = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    try:
        item = repo.create_item(db, business.id, body.model_dump())
        write_audit(db, business_id=business.id, actor_id=user.id, entity="menu_item",
                    entity_id=str(item.id), action="create", payload=body.model_dump(mode="json"))
        db.commit()
    except repo.MenuError as exc:
        db.rollback()
        raise _menu_error(exc) from None
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Duplicate item, variant or alias in this business") from None
    db.refresh(item)
    return item


@router.get("/items/{item_id}", response_model=ItemRead)
def get_item(
    item_id: uuid.UUID,
    _: User = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    return _item_or_404(db, business.id, item_id)


@router.patch("/items/{item_id}", response_model=ItemRead)
def update_item(
    item_id: uuid.UUID,
    body: ItemUpdate,
    user: User = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    item = _item_or_404(db, business.id, item_id)
    changes = body.model_dump(exclude_unset=True, mode="json")
    try:
        if "category" in changes:
            repo.ensure_category(db, business.id, changes["category"])
        repo.update_item(item, changes)
        write_audit(db, business_id=business.id, actor_id=user.id, entity="menu_item",
                    entity_id=str(item.id), action="update", payload=changes)
        db.commit()
    except repo.MenuError as exc:
        db.rollback()
        raise _menu_error(exc) from None
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Duplicate item name in this category") from None
    db.refresh(item)
    return item


@router.post("/items/{item_id}/availability", response_model=ItemRead)
def set_availability(
    item_id: uuid.UUID,
    body: AvailabilityIn,
    user: User = Depends(require(P.MENU_TOGGLE_AVAILABILITY)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    """The kitchen's sold-out switch (F6). It does not touch open orders; the Phase 1
    bot does not read it (B2)."""
    item = _item_or_404(db, business.id, item_id)
    repo.set_availability(item, body.is_available)
    write_audit(db, business_id=business.id, actor_id=user.id, entity="menu_item",
                entity_id=str(item.id), action="update",
                payload={"is_available": body.is_available})
    db.commit()
    db.refresh(item)
    return item


@router.post("/items/{item_id}/variants", response_model=VariantRead,
             status_code=status.HTTP_201_CREATED)
def add_variant(
    item_id: uuid.UUID,
    body: VariantIn,
    user: User = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    item = _item_or_404(db, business.id, item_id)
    try:
        variant = repo.add_variant(db, item, body.model_dump())
        write_audit(db, business_id=business.id, actor_id=user.id, entity="menu_variant",
                    entity_id=str(variant.id), action="create",
                    payload={**body.model_dump(mode="json"), "item_id": str(item.id)})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Variant already exists") from None
    return variant


@router.patch("/items/{item_id}/variants/{variant_id}", response_model=VariantRead)
def update_variant(
    item_id: uuid.UUID,
    variant_id: uuid.UUID,
    body: VariantUpdate,
    user: User = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    """Changing the price of an existing variant needs PRICE_MANAGE on top of
    MENU_MANAGE (F1): prices are money, names and ordering are structure."""
    item = _item_or_404(db, business.id, item_id)
    variant = repo.get_variant(db, business.id, item.id, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Variant not found")
    changes = body.model_dump(exclude_unset=True, mode="json")
    if repo.variant_price_changed(variant, body.price, "price" in changes):
        from app.permissions import role_has
        if not role_has(user.role, P.PRICE_MANAGE):
            raise HTTPException(status_code=403, detail="Missing permission")
    try:
        repo.update_variant(db, item, variant, changes)
        write_audit(db, business_id=business.id, actor_id=user.id, entity="menu_variant",
                    entity_id=str(variant.id), action="update", payload=changes)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Variant already exists") from None
    return variant


@router.post("/items/{item_id}/variants/{variant_id}/default", response_model=VariantRead)
def set_default_variant(
    item_id: uuid.UUID,
    variant_id: uuid.UUID,
    user: User = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    item = _item_or_404(db, business.id, item_id)
    variant = repo.get_variant(db, business.id, item.id, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Variant not found")
    repo.set_default_variant(db, item, variant)
    write_audit(db, business_id=business.id, actor_id=user.id, entity="menu_variant",
                entity_id=str(variant.id), action="update",
                payload={"is_default": True, "item_id": str(item.id)})
    db.commit()
    return variant


@router.delete("/items/{item_id}/variants/{variant_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_variant(
    item_id: uuid.UUID,
    variant_id: uuid.UUID,
    user: User = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    item = _item_or_404(db, business.id, item_id)
    variant = repo.get_variant(db, business.id, item.id, variant_id)
    if variant is None:
        raise HTTPException(status_code=404, detail="Variant not found")
    try:
        repo.delete_variant(db, item, variant)
        write_audit(db, business_id=business.id, actor_id=user.id, entity="menu_variant",
                    entity_id=str(variant_id), action="delete",
                    payload={"item_id": str(item.id)})
        db.commit()
    except repo.MenuError as exc:
        db.rollback()
        raise _menu_error(exc) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/items/{item_id}/aliases", response_model=AliasRead,
             status_code=status.HTTP_201_CREATED)
def add_alias(
    item_id: uuid.UUID,
    body: AliasIn,
    user: User = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    item = _item_or_404(db, business.id, item_id)
    try:
        row = repo.add_alias(db, item, body.alias.strip())
        write_audit(db, business_id=business.id, actor_id=user.id, entity="menu_alias",
                    entity_id=str(row.id), action="create",
                    payload={"alias": body.alias, "item_id": str(item.id)})
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="Alias already exists in this business") from None
    return row


@router.delete("/items/{item_id}/aliases/{alias_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alias(
    item_id: uuid.UUID,
    alias_id: uuid.UUID,
    user: User = Depends(require(P.MENU_MANAGE)),
    business: Business = Depends(get_business), db: Session = Depends(get_db),
):
    _item_or_404(db, business.id, item_id)
    if not repo.delete_alias(db, business.id, item_id, alias_id):
        raise HTTPException(status_code=404, detail="Alias not found")
    write_audit(db, business_id=business.id, actor_id=user.id, entity="menu_alias",
                entity_id=str(alias_id), action="delete", payload={"item_id": str(item_id)})
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
