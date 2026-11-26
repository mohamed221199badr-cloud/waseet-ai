"""Tenant-scoped Orders: draft -> pending -> confirmed | rejected."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_business, require
from app.audit import write_audit
from app.db import get_db
from app.models import Business
from app.permissions import Permission as P
from app.repositories import customers as customers_repo
from app.repositories import inventory
from app.repositories import orders as repo
from app.schemas_orders import ItemIn, OrderCreate, OrderRead, RejectBody

router = APIRouter(prefix="/orders", tags=["orders"])


def _order_or_404(db: Session, business_id: uuid.UUID, order_id: uuid.UUID, lock: bool = False):
    order = repo.get_order(db, business_id, order_id, lock=lock)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return order


def _add_item_or_http(db: Session, order, item: ItemIn):
    try:
        return repo.add_item(db, order, item.product_id, item.quantity)
    except repo.ProductUnavailable:
        db.rollback()
        raise HTTPException(status_code=404, detail="Product not found or inactive") from None
    except repo.DuplicateItem:
        db.rollback()
        raise HTTPException(status_code=409, detail="Product already in this order") from None
    except repo.OrderStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None


@router.get("", response_model=list[OrderRead])
def list_orders(
    status_filter: str | None = Query(default=None, alias="status"),
    _: None = Depends(require(P.ORDER_VIEW_ALL)),
    customer_id: uuid.UUID | None = None,
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    return repo.list_orders(db, business.id, status_filter, customer_id)


@router.post("", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
def create_order(
    body: OrderCreate,
    _: None = Depends(require(P.ORDER_CREATE_MANUAL)),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    if customers_repo.get_customer(db, business.id, body.customer_id) is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    data = body.model_dump(exclude={"items"})
    order = repo.create_order(db, business.id, data)
    for item in body.items:
        _add_item_or_http(db, order, item)
    write_audit(
        db, business_id=business.id, entity="order", entity_id=str(order.id), action="create",
        payload=body.model_dump(mode="json"),
    )
    db.commit()
    db.refresh(order)
    return order


@router.get("/{order_id}", response_model=OrderRead)
def get_order(
    order_id: uuid.UUID,
    _: None = Depends(require(P.ORDER_VIEW_ALL)),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    return _order_or_404(db, business.id, order_id)


@router.post("/{order_id}/items", response_model=OrderRead, status_code=status.HTTP_201_CREATED)
def add_item(
    order_id: uuid.UUID,
    body: ItemIn,
    _: None = Depends(require(P.ORDER_EDIT_LINES)),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    order = _order_or_404(db, business.id, order_id, lock=True)
    item = _add_item_or_http(db, order, body)
    write_audit(
        db, business_id=business.id, entity="order_item", entity_id=str(item.id),
        action="create",
        payload={"order_id": str(order.id), "product_id": str(body.product_id),
                 "quantity": body.quantity, "unit_price": str(item.unit_price),
                 "price_source": item.price_source},
    )
    db.commit()
    db.refresh(order)
    return order


@router.delete("/{order_id}/items/{item_id}", response_model=OrderRead)
def remove_item(
    order_id: uuid.UUID,
    item_id: uuid.UUID,
    _: None = Depends(require(P.ORDER_EDIT_LINES)),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    order = _order_or_404(db, business.id, order_id, lock=True)
    try:
        item = repo.remove_item(db, order, item_id)
    except repo.OrderStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found")
    write_audit(
        db, business_id=business.id, entity="order_item", entity_id=str(item_id),
        action="delete", payload={"order_id": str(order.id)},
    )
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/submit", response_model=OrderRead)
def submit_order(
    order_id: uuid.UUID,
    _: None = Depends(require(P.ORDER_CREATE_MANUAL)),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    order = _order_or_404(db, business.id, order_id, lock=True)
    try:
        repo.submit(db, order)
    except repo.OrderStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    write_audit(
        db, business_id=business.id, entity="order", entity_id=str(order.id), action="update",
        payload={"status": "awaiting_confirmation", "total": str(order.total)},
    )
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/confirm", response_model=OrderRead)
def confirm_order(
    order_id: uuid.UUID,
    _: None = Depends(require(P.ORDER_CONFIRM)),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    order = _order_or_404(db, business.id, order_id, lock=True)
    try:
        repo.confirm(db, order)
    except repo.OrderStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except inventory.InsufficientStock as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"Insufficient stock for product {exc.product_id}: "
                   f"available {exc.available}, requested {exc.requested}",
        ) from None
    write_audit(
        db, business_id=business.id, entity="order", entity_id=str(order.id), action="update",
        payload={"status": "confirmed", "total": str(order.total)},
    )
    db.commit()
    db.refresh(order)
    return order


@router.post("/{order_id}/reject", response_model=OrderRead)
def reject_order(
    order_id: uuid.UUID,
    body: RejectBody | None = None,
    _: None = Depends(require(P.ORDER_REJECT)),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    order = _order_or_404(db, business.id, order_id, lock=True)
    reason = body.reason if body else None
    try:
        repo.reject(db, order, reason)
    except repo.OrderStateError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    write_audit(
        db, business_id=business.id, entity="order", entity_id=str(order.id), action="update",
        payload={"status": "rejected", "reason": reason},
    )
    db.commit()
    db.refresh(order)
    return order


@router.delete("/{order_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_draft(
    order_id: uuid.UUID,
    _: None = Depends(require(P.ORDER_EDIT_LINES)),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    order = _order_or_404(db, business.id, order_id, lock=True)
    if order.status != "draft":
        raise HTTPException(status_code=409, detail="Only draft orders can be deleted")
    write_audit(
        db, business_id=business.id, entity="order", entity_id=str(order.id), action="delete",
        payload={"customer_id": str(order.customer_id)},
    )
    db.delete(order)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
