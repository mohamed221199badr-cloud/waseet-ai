"""Text -> draft order. Matching is delegated to app.matching (pure); this layer only
reads the catalog, writes the draft and records exceptions. Prices come from the
existing resolve_price (customer price > catalog price); stock is never touched."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.matching import CatalogEntry, Matcher, MatchResult
from app.models import Product, ProductAlias
from app.models_orders import Customer, Order, OrderException
from app.repositories import orders as orders_repo

MAX_LINES = 60


def build_matcher(db: Session, business_id: uuid.UUID) -> Matcher:
    products = list(
        db.scalars(
            select(Product).where(Product.business_id == business_id, Product.is_active.is_(True))
        )
    )
    aliases: dict[uuid.UUID, list[str]] = {}
    for a in db.scalars(select(ProductAlias).where(ProductAlias.business_id == business_id)):
        aliases.setdefault(a.product_id, []).append(a.alias)
    return Matcher(
        [CatalogEntry(p.id, p.sku, p.name, aliases.get(p.id, [])) for p in products]
    )


def find_customer_by_phone(db: Session, business_id: uuid.UUID, phone: str) -> Customer | None:
    return db.scalar(
        select(Customer).where(Customer.business_id == business_id, Customer.phone == phone.strip())
    )


def _candidates_json(result: MatchResult) -> list[dict]:
    return [
        {"product_id": str(c.product_id), "sku": c.sku, "name": c.name, "score": c.score,
         "kind": c.kind}
        for c in result.candidates
    ]


def create_draft_from_text(
    db: Session, business_id: uuid.UUID, customer: Customer, text: str, channel: str
) -> tuple[Order, list[MatchResult]]:
    results = build_matcher(db, business_id).match_text(text)
    if len(results) > MAX_LINES:
        raise ValueError(f"too many lines (max {MAX_LINES})")
    order = orders_repo.create_order(
        db, business_id,
        {"customer_id": customer.id, "channel": channel, "source_text": text, "notes": None},
    )
    seen: set[uuid.UUID] = set()
    for r in results:
        if r.status == "matched":
            if r.product_id in seen:
                # same product named twice: never merge or overwrite silently
                r.status, r.reason = "exception", "duplicate_product"
            else:
                seen.add(r.product_id)
                orders_repo.add_item(db, order, r.product_id, r.quantity)
                continue
        db.add(
            OrderException(
                business_id=business_id, order_id=order.id, raw_text=r.raw[:500],
                reason=r.reason or "not_found", quantity=r.quantity,
                candidates=_candidates_json(r) if r.candidates else (
                    [{"product_id": str(r.product_id)}] if r.product_id else []
                ),
            )
        )
    db.flush()
    db.refresh(order)
    return order, results


def get_exception(
    db: Session, business_id: uuid.UUID, order_id: uuid.UUID, exception_id: uuid.UUID
) -> OrderException | None:
    return db.scalar(
        select(OrderException).where(
            OrderException.business_id == business_id,
            OrderException.order_id == order_id,
            OrderException.id == exception_id,
        )
    )


def close_exception(exc: OrderException, status: str) -> None:
    exc.status = status
    exc.decided_at = datetime.now(timezone.utc)
