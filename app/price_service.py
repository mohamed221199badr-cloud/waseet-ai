"""Price resolution (Phase 1, task 1.7, spec B4). Data-entry phase: the bot keeps
quoting `products` until the Phase 2 cutover; this service resolves prices for the new
menu tables and is covered by unit tests.

resolve() returns a price and its source, or None when nothing can be quoted. None
happens only when no active override applies AND the base price is NULL - then an
order_exception(reason='price_required') is created and the bot never quotes.

Order of evaluation (B4):
1. active customer override (concrete price) -> 'override'
2. active segment override -> 'override'
3. base variant price if not NULL -> 'menu'
4. None -> price_required exception
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models_menu import MenuItemVariant, PriceOverride
from app.models_orders import OrderException


@dataclass
class ResolvedPrice:
    price: Decimal | None
    source: str  # 'override' | 'menu' | 'none'
    override: PriceOverride | None = None


def _active_overrides(db: Session, business_id: uuid.UUID, variant_id: uuid.UUID,
                      at: datetime) -> list[PriceOverride]:
    rows = db.scalars(select(PriceOverride).where(
        PriceOverride.business_id == business_id,
        PriceOverride.variant_id == variant_id,
        PriceOverride.is_active.is_(True))).all()
    return [o for o in rows
            if (o.starts_at is None or o.starts_at <= at)
            and (o.ends_at is None or o.ends_at > at)]


def resolve_price(db: Session, variant: MenuItemVariant, *,
                  customer_id: uuid.UUID | None = None,
                  segment: str | None = None,
                  at: datetime,
                  order_id: uuid.UUID | None = None) -> ResolvedPrice:
    """B4 resolution order. When order_id is given and nothing resolves, a
    price_required exception is raised on that order (the bot never quotes)."""
    """B4 resolution order. Customer overrides win over segment overrides."""
    overrides = _active_overrides(db, variant.business_id, variant.id, at)
    if customer_id is not None:
        for o in overrides:
            if o.scope == "customer" and o.customer_id == customer_id:
                return ResolvedPrice(o.price, "override", o)
    if segment:
        for o in overrides:
            if o.scope == "segment" and o.segment == segment:
                return ResolvedPrice(o.price, "override", o)
    if variant.price is not None:
        return ResolvedPrice(variant.price, "menu")
    if order_id is not None:
        db.add(OrderException(business_id=variant.business_id, order_id=order_id,
                              raw_text="",
                              reason="price_required",
                              reason_detail="null_base_price",
                              customer_message="سنأكد السعر مع الكاشير"))
        db.flush()
    return ResolvedPrice(None, "none")
