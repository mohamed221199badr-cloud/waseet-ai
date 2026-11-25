"""Repository layer: every query is scoped to one business_id.

The API never queries models directly; tenant isolation lives here, and the
composite FK (business_id, product_id) enforces it at the database level too.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, ProductAlias


def list_products(db: Session, business_id: uuid.UUID) -> list[Product]:
    return list(
        db.scalars(
            select(Product).where(Product.business_id == business_id).order_by(Product.created_at)
        )
    )


def get_product(db: Session, business_id: uuid.UUID, product_id: uuid.UUID) -> Product | None:
    return db.scalar(
        select(Product).where(
            Product.business_id == business_id,
            Product.id == product_id,
        )
    )


def create_product(db: Session, business_id: uuid.UUID, data: dict) -> Product:
    product = Product(business_id=business_id, **data)
    db.add(product)
    db.flush()  # assigns id; IntegrityError for duplicate (business_id, sku) surfaces here
    return product


def update_product(product: Product, data: dict) -> Product:
    for key, value in data.items():
        setattr(product, key, value)
    return product


def delete_product(db: Session, product: Product) -> None:
    db.delete(product)


def list_aliases(db: Session, business_id: uuid.UUID, product_id: uuid.UUID) -> list[ProductAlias]:
    return list(
        db.scalars(
            select(ProductAlias).where(
                ProductAlias.business_id == business_id,
                ProductAlias.product_id == product_id,
            )
        )
    )


def create_alias(
    db: Session, business_id: uuid.UUID, product_id: uuid.UUID, alias: str
) -> ProductAlias:
    row = ProductAlias(business_id=business_id, product_id=product_id, alias=alias)
    db.add(row)
    db.flush()
    return row
