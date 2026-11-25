"""Seed two synthetic businesses with users, products and aliases. Idempotent.

Run: python -m app.seed
"""

import os

from sqlalchemy import select

from app.auth import hash_password
from app.db import SessionLocal
from app.models import Business, Product, ProductAlias, User

SEED = [
    {
        "slug": "synthetic-foods-co",
        "name": "شركة النيل للمواد الغذائية (تجريبية)",
        "user": {"full_name": "مالك تجريبي", "email": "owner@synthetic-foods.test"},
        "products": [
            {"sku": "RICE-5KG", "name": "أرز مصري 5 كيلو", "unit": "bag", "price": "185.00",
             "stock_quantity": 120, "aliases": ["رز 5 كيلو", "ارز"]},
            {"sku": "OIL-1L", "name": "زيت عباد الشمس 1 لتر", "unit": "bottle", "price": "62.50",
             "stock_quantity": 300, "aliases": ["زيت", "زيت عباد"]},
            {"sku": "SUGAR-1KG", "name": "سكر 1 كيلو", "unit": "pack", "price": "28.00",
             "stock_quantity": 500, "aliases": ["سكر"]},
        ],
    },
    {
        "slug": "synthetic-plastics-co",
        "name": "شركة الدلتا للبلاستيك (تجريبية)",
        "user": {"full_name": "مالك تجريبي ٢", "email": "owner@synthetic-plastics.test"},
        "products": [
            {"sku": "CUP-200ML", "name": "كوب بلاستيك 200 مل", "unit": "carton", "price": "95.00",
             "stock_quantity": 80, "aliases": ["كوبايات 200", "أكواب"]},
            {"sku": "PLATE-22", "name": "طبق بلاستيك 22 سم", "unit": "carton", "price": "110.00",
             "stock_quantity": 60, "aliases": ["أطباق", "اطباق 22"]},
        ],
    },
]


def run() -> None:
    db = SessionLocal()
    try:
        for item in SEED:
            business = db.scalar(select(Business).where(Business.slug == item["slug"]))
            if business is None:
                business = Business(name=item["name"], slug=item["slug"])
                db.add(business)
                db.flush()
            user = db.scalar(
                select(User).where(
                    User.business_id == business.id, User.email == item["user"]["email"]
                )
            )
            if user is None:
                user = User(business_id=business.id, role="owner", **item["user"])
                db.add(user)
            if not user.password_hash:
                # synthetic demo accounts only; override with SEED_PASSWORD
                user.password_hash = hash_password(os.environ.get("SEED_PASSWORD", "demo-pass-123"))
            for p in item["products"]:
                product = db.scalar(
                    select(Product).where(
                        Product.business_id == business.id, Product.sku == p["sku"]
                    )
                )
                if product is None:
                    product = Product(
                        business_id=business.id,
                        sku=p["sku"],
                        name=p["name"],
                        unit=p["unit"],
                        price=p["price"],
                        stock_quantity=p["stock_quantity"],
                    )
                    db.add(product)
                    db.flush()
                existing = {
                    a.alias
                    for a in db.scalars(
                        select(ProductAlias).where(ProductAlias.product_id == product.id)
                    )
                }
                for alias in p["aliases"]:
                    if alias not in existing:
                        db.add(
                            ProductAlias(
                                business_id=business.id, product_id=product.id, alias=alias
                            )
                        )
            db.commit()
            print(f"seeded: {business.slug} ({business.id})")
    finally:
        db.close()


if __name__ == "__main__":
    run()
