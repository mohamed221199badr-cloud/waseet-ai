"""Tenant-scoped Customers + customer-specific prices."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_business
from app.audit import write_audit
from app.db import get_db
from app.models import Business
from app.repositories import customers as repo
from app.repositories import products as products_repo
from app.schemas_orders import (
    CustomerCreate,
    CustomerPriceRead,
    CustomerPriceSet,
    CustomerRead,
    CustomerUpdate,
)

router = APIRouter(prefix="/customers", tags=["customers"])


def _customer_or_404(db: Session, business_id: uuid.UUID, customer_id: uuid.UUID):
    row = repo.get_customer(db, business_id, customer_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Customer not found")
    return row


@router.get("", response_model=list[CustomerRead])
def list_customers(business: Business = Depends(get_business), db: Session = Depends(get_db)):
    return repo.list_customers(db, business.id)


@router.post("", response_model=CustomerRead, status_code=status.HTTP_201_CREATED)
def create_customer(
    body: CustomerCreate,
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    try:
        row = repo.create_customer(db, business.id, body.model_dump())
        write_audit(
            db, business_id=business.id, entity="customer", entity_id=str(row.id),
            action="create", payload=body.model_dump(mode="json"),
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="A customer with this phone already exists in this business"
        ) from None
    return row


@router.get("/{customer_id}", response_model=CustomerRead)
def get_customer(
    customer_id: uuid.UUID,
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    return _customer_or_404(db, business.id, customer_id)


@router.patch("/{customer_id}", response_model=CustomerRead)
def update_customer(
    customer_id: uuid.UUID,
    body: CustomerUpdate,
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    row = _customer_or_404(db, business.id, customer_id)
    changes = body.model_dump(exclude_unset=True, mode="json")
    try:
        repo.update_customer(row, changes)
        write_audit(
            db, business_id=business.id, entity="customer", entity_id=str(row.id),
            action="update", payload=changes,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409, detail="A customer with this phone already exists in this business"
        ) from None
    return row


@router.get("/{customer_id}/prices", response_model=list[CustomerPriceRead])
def list_prices(
    customer_id: uuid.UUID,
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    _customer_or_404(db, business.id, customer_id)
    return repo.list_prices(db, business.id, customer_id)


@router.put("/{customer_id}/prices/{product_id}", response_model=CustomerPriceRead)
def set_price(
    customer_id: uuid.UUID,
    product_id: uuid.UUID,
    body: CustomerPriceSet,
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    _customer_or_404(db, business.id, customer_id)
    if products_repo.get_product(db, business.id, product_id) is None:
        raise HTTPException(status_code=404, detail="Product not found")
    row = repo.upsert_price(db, business.id, customer_id, product_id, body.price)
    write_audit(
        db, business_id=business.id, entity="customer_price",
        entity_id=str(row.id), action="update",
        payload={"customer_id": str(customer_id), "product_id": str(product_id),
                 "price": str(body.price)},
    )
    db.commit()
    return row


@router.delete("/{customer_id}/prices/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_price(
    customer_id: uuid.UUID,
    product_id: uuid.UUID,
    business: Business = Depends(get_business),
    db: Session = Depends(get_db),
):
    _customer_or_404(db, business.id, customer_id)
    row = repo.get_customer_price(db, business.id, customer_id, product_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Customer price not found")
    write_audit(
        db, business_id=business.id, entity="customer_price",
        entity_id=str(row.id), action="delete",
        payload={"customer_id": str(customer_id), "product_id": str(product_id),
                 "price": str(row.price)},
    )
    repo.delete_price(db, row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
