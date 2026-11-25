"""Customers and customer-specific prices. Every query is scoped to one business_id."""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models_orders import Customer, CustomerPrice


def list_customers(db: Session, business_id: uuid.UUID) -> list[Customer]:
    return list(
        db.scalars(
            select(Customer)
            .where(Customer.business_id == business_id)
            .order_by(Customer.created_at)
        )
    )


def get_customer(db: Session, business_id: uuid.UUID, customer_id: uuid.UUID) -> Customer | None:
    return db.scalar(
        select(Customer).where(Customer.business_id == business_id, Customer.id == customer_id)
    )


def create_customer(db: Session, business_id: uuid.UUID, data: dict) -> Customer:
    row = Customer(business_id=business_id, **data)
    db.add(row)
    db.flush()
    return row


def update_customer(customer: Customer, data: dict) -> Customer:
    for key, value in data.items():
        setattr(customer, key, value)
    return customer


def list_prices(db: Session, business_id: uuid.UUID, customer_id: uuid.UUID) -> list[CustomerPrice]:
    return list(
        db.scalars(
            select(CustomerPrice).where(
                CustomerPrice.business_id == business_id,
                CustomerPrice.customer_id == customer_id,
            )
        )
    )


def get_customer_price(
    db: Session, business_id: uuid.UUID, customer_id: uuid.UUID, product_id: uuid.UUID
) -> CustomerPrice | None:
    return db.scalar(
        select(CustomerPrice).where(
            CustomerPrice.business_id == business_id,
            CustomerPrice.customer_id == customer_id,
            CustomerPrice.product_id == product_id,
        )
    )


def upsert_price(
    db: Session, business_id: uuid.UUID, customer_id: uuid.UUID, product_id: uuid.UUID,
    price: Decimal,
) -> CustomerPrice:
    stmt = (
        insert(CustomerPrice)
        .values(
            id=uuid.uuid4(), business_id=business_id, customer_id=customer_id,
            product_id=product_id, price=price,
        )
        .on_conflict_do_update(constraint="uq_cprice_unique", set_={"price": price})
    )
    db.execute(stmt)
    db.flush()
    row = get_customer_price(db, business_id, customer_id, product_id)
    db.refresh(row)
    return row


def delete_price(db: Session, row: CustomerPrice) -> None:
    db.delete(row)
