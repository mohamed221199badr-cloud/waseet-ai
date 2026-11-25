"""Two confirmations racing for the last units: exactly one may win."""

import threading
from decimal import Decimal

from sqlalchemy.orm import sessionmaker


def test_concurrent_confirm_does_not_oversell(engine, db_session, two_businesses):
    from app.models import Product
    from app.models_orders import Customer, Order, OrderItem
    from app.repositories import orders as repo

    ids = two_businesses
    cust = Customer(business_id=ids["a"], name="ع")
    db_session.add(cust)
    db_session.flush()
    order_ids = []
    for _ in range(2):  # stock is 5; each order wants 4
        o = Order(business_id=ids["a"], customer_id=cust.id, status="pending")
        db_session.add(o)
        db_session.flush()
        db_session.add(OrderItem(
            business_id=ids["a"], order_id=o.id, product_id=ids["product_a"], quantity=4,
            unit_price=Decimal("10"), line_total=Decimal("40")))
        order_ids.append(o.id)
    db_session.commit()

    Session = sessionmaker(bind=engine)
    results = []

    def worker(oid):
        s = Session()
        try:
            order = repo.get_order(s, ids["a"], oid, lock=True)
            repo.confirm(s, order)
            s.commit()
            results.append("ok")
        except Exception:
            s.rollback()
            results.append("fail")
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(oid,)) for oid in order_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert sorted(results) == ["fail", "ok"]
    db_session.expire_all()
    assert db_session.get(Product, ids["product_a"]).stock_quantity == 1
