"""Phase 2 cutover: the chat agent's read path over the restaurant menu tables.

The agent matches at ITEM level (same semantics the products matcher had), then resolves
the variant inside the conversation: one variant picks itself, several variants ask.
Prices follow B4: an active customer override wins (`override`), then the variant base
price (`menu`), and NULL with no override means "ask the cashier" - never quoted, never
added to a draft. Segment overrides are part of the schema but Customer has no segment
yet, so only customer-scope overrides apply here.

Everything is tenant-scoped: a variant/override of another business is invisible.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.matching.engine import CatalogEntry, Matcher
from app.models_menu import (
    MenuCategory,
    MenuItem,
    MenuItemAlias,
    MenuItemVariant,
    PriceOverride,
)

PRICE_SOURCES = ("menu", "override", "cashier_manual")


@dataclass
class Catalog:
    """The menu of one business, loaded once per message."""

    items: dict[str, MenuItem]                 # str(item.id) -> item
    variants: dict[str, list[MenuItemVariant]]  # str(item.id) -> variants, sort_order
    matcher: Matcher

    def item(self, item_id: str) -> MenuItem | None:
        return self.items.get(item_id)

    def variant(self, item_id: str, variant_id: str) -> MenuItemVariant | None:
        return next((v for v in self.variants.get(item_id, []) if str(v.id) == variant_id), None)


def has_menu(db: Session, business_id: uuid.UUID) -> bool:
    """The cutover switch: a business is on the menu once it has at least one available
    item with at least one variant. Below that the bot keeps reading `products`."""
    return db.scalar(
        select(MenuItemVariant.id)
        .join(MenuItem, (MenuItem.business_id == MenuItemVariant.business_id)
              & (MenuItem.id == MenuItemVariant.item_id))
        .where(MenuItem.business_id == business_id, MenuItem.is_available.is_(True))
        .limit(1)
    ) is not None


def load_catalog(db: Session, business_id: uuid.UUID) -> Catalog:
    items = list(db.scalars(select(MenuItem).where(
        MenuItem.business_id == business_id, MenuItem.is_available.is_(True))))
    aliases: dict[uuid.UUID, list[str]] = {}
    for a in db.scalars(select(MenuItemAlias).where(MenuItemAlias.business_id == business_id)):
        aliases.setdefault(a.item_id, []).append(a.alias)
    by_id = {str(i.id): i for i in items}
    variants = {str(i.id): sorted(i.variants, key=lambda v: v.sort_order) for i in items}
    matcher = Matcher([
        CatalogEntry(i.id, "", i.name, aliases.get(i.id, [])) for i in items
    ])
    return Catalog(items=by_id, variants=variants, matcher=matcher)


def list_categories(db: Session, business_id: uuid.UUID) -> list[MenuCategory]:
    """Categories that actually have available items, in display order."""
    rows = db.scalars(select(MenuCategory).where(
        MenuCategory.business_id == business_id).order_by(MenuCategory.sort_order)).all()
    cat = load_catalog(db, business_id)
    have = {i.category for i in cat.items.values()}
    return [c for c in rows if c.name in have]


def category_items(cat: Catalog, category: str) -> list[MenuItem]:
    return sorted((i for i in cat.items.values() if i.category == category),
                  key=lambda i: i.sort_order)


def resolve_variant_price(db: Session, business_id: uuid.UUID, customer_id: uuid.UUID,
                          variant: MenuItemVariant) -> tuple[Decimal | None, str | None]:
    """B4: active customer override -> base price -> None (cashier). Never trusts input:
    the override must belong to this business, this variant and this customer."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    override = db.scalar(
        select(PriceOverride).where(
            PriceOverride.business_id == business_id,
            PriceOverride.variant_id == variant.id,
            PriceOverride.scope == "customer",
            PriceOverride.customer_id == customer_id,
            PriceOverride.is_active.is_(True),
            (PriceOverride.starts_at.is_(None)) | (PriceOverride.starts_at <= now),
            (PriceOverride.ends_at.is_(None)) | (PriceOverride.ends_at > now),
        ).limit(1))
    if override is not None:
        return Decimal(override.price), "override"
    if variant.price is not None:
        return Decimal(variant.price), "menu"
    return None, None


def default_variant(cat: Catalog, item_id: str) -> MenuItemVariant | None:
    vs = cat.variants.get(item_id, [])
    return next((v for v in vs if v.is_default), vs[0] if vs else None)
