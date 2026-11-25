"""Milestone 2 seed: customers, special prices and one pending order per synthetic business.
Idempotent. Run after app.seed:  python -m app.seed_orders
"""

from decimal import Decimal

from sqlalchemy import select

from app.db import SessionLocal
from app.models import Business, Product
from app.models_orders import Customer, CustomerPrice, Order
from app.repositories import orders as orders_repo

SEED = {
    "synthetic-foods-co": {
        "customers": [
            {"name": "سوبر ماركت الأمل (تجريبي)", "phone": "01000000011"},
            {"name": "بقالة السلام (تجريبي)", "phone": "01000000012"},
        ],
        "prices": {"01000000011": {"RICE-5KG": "178.00", "OIL-1L": "60.00"}},
        "order": {"phone": "01000000011", "items": {"RICE-5KG": 10, "OIL-1L": 6}},
    },
    "synthetic-plastics-co": {
        "customers": [{"name": "مطعم الرحاب (تجريبي)", "phone": "01000000021"}],
        "prices": {"01000000021": {"CUP-200ML": "90.00"}},
        "order": {"phone": "01000000021", "items": {"CUP-200ML": 5, "PLATE-22": 2}},
    },
}


def run() -> None:
    db = SessionLocal()
    try:
        for slug, cfg in SEED.items():
            biz = db.scalar(select(Business).where(Business.slug == slug))
            if biz is None:
                print(f"skip {slug}: run python -m app.seed first")
                continue
            by_phone = {}
            for c in cfg["customers"]:
                row = db.scalar(
                    select(Customer).where(
                        Customer.business_id == biz.id, Customer.phone == c["phone"]
                    )
                )
                if row is None:
                    row = Customer(business_id=biz.id, **c)
                    db.add(row)
                    db.flush()
                by_phone[c["phone"]] = row
            products = {
                p.sku: p for p in db.scalars(select(Product).where(Product.business_id == biz.id))
            }
            for phone, prices in cfg["prices"].items():
                for sku, price in prices.items():
                    exists = db.scalar(
                        select(CustomerPrice).where(
                            CustomerPrice.business_id == biz.id,
                            CustomerPrice.customer_id == by_phone[phone].id,
                            CustomerPrice.product_id == products[sku].id,
                        )
                    )
                    if exists is None:
                        db.add(CustomerPrice(
                            business_id=biz.id, customer_id=by_phone[phone].id,
                            product_id=products[sku].id, price=Decimal(price),
                        ))
            db.flush()
            cust = by_phone[cfg["order"]["phone"]]
            has_order = db.scalar(
                select(Order.id).where(
                    Order.business_id == biz.id, Order.customer_id == cust.id,
                    Order.source_text == "seed",
                )
            )
            if has_order is None:
                order = orders_repo.create_order(
                    db, biz.id,
                    {"customer_id": cust.id, "channel": "manual", "source_text": "seed"},
                )
                for sku, qty in cfg["order"]["items"].items():
                    orders_repo.add_item(db, order, products[sku].id, qty)
                orders_repo.submit(db, order)
            print(f"{slug}: customers={len(by_phone)} ok")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    run()
