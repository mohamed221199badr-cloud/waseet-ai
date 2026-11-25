"""Tenant-scoped inventory: manual stock changes, ledger, low-stock list."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.deps import get_business
from app.audit import write_audit
from app.db import get_db
from app.models import Business
from app.repositories import inventory as repo
from app.schemas_orders import MovementRead, StockAdjust, StockRead

router = APIRouter(tags=["inventory"])


@router.post("/products/{product_id}/stock", response_model=StockRead)
def adjust_stock(
    product_id: uuid.UUID,
    body: StockAdjust,
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    if body.delta == 0:
        raise HTTPException(status_code=422, detail="delta must not be zero")
    product = repo.lock_product(db, business.id, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    try:
        movement = repo.apply_delta(db, product, body.delta, body.reason, note=body.note)
    except repo.InsufficientStock as exc:
        db.rollback()
        raise HTTPException(
            status_code=409, detail=f"Stock cannot go below zero (available {exc.available})"
        ) from None
    write_audit(
        db, business_id=business.id, entity="stock", entity_id=str(product.id),
        action="update",
        payload={"delta": body.delta, "reason": body.reason,
                 "balance_after": movement.balance_after},
    )
    db.commit()
    return StockRead(
        product_id=product.id, sku=product.sku, name=product.name,
        stock_quantity=product.stock_quantity,
    )


@router.get("/products/{product_id}/movements", response_model=list[MovementRead])
def movements(
    product_id: uuid.UUID,
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    from app.repositories import products as products_repo

    if products_repo.get_product(db, business.id, product_id) is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return repo.list_movements(db, business.id, product_id)


@router.get("/inventory/low-stock", response_model=list[StockRead])
def low_stock(
    threshold: int = Query(default=10, ge=0),
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    return [
        StockRead(product_id=p.id, sku=p.sku, name=p.name, stock_quantity=p.stock_quantity)
        for p in repo.low_stock(db, business.id, threshold)
    ]
