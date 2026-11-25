"""Menu repository and rules (Phase 1, tasks 1.4). Data-entry only: nothing here is
read by the bot, orders or inventory until the Phase 2 cutover (B2).

Rules enforced here (not scattered in routes):
- an item always has at least one variant and exactly one default variant;
- sold_by=weight requires a unit_label (also a DB CHECK);
- setting a default unset the others in the same transaction.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models_menu import MenuCategory, MenuItem, MenuItemAlias, MenuItemVariant


class MenuError(Exception):
    """A menu rule was violated (409 in the API)."""


def list_categories(db: Session, business_id: uuid.UUID) -> list[MenuCategory]:
    return list(db.scalars(
        select(MenuCategory).where(MenuCategory.business_id == business_id)
        .order_by(MenuCategory.sort_order, MenuCategory.name)))


def ensure_category(db: Session, business_id: uuid.UUID, name: str,
                    sort_order: int = 0) -> MenuCategory:
    row = db.scalar(select(MenuCategory).where(
        MenuCategory.business_id == business_id, MenuCategory.name == name))
    if row is None:
        row = MenuCategory(business_id=business_id, name=name, sort_order=sort_order)
        db.add(row)
        db.flush()
    return row


def list_items(db: Session, business_id: uuid.UUID,
               category: str | None = None) -> list[MenuItem]:
    stmt = select(MenuItem).where(MenuItem.business_id == business_id)
    if category:
        stmt = stmt.where(MenuItem.category == category)
    return list(db.scalars(stmt.order_by(MenuItem.category, MenuItem.sort_order,
                                         MenuItem.name)))


def get_item(db: Session, business_id: uuid.UUID, item_id: uuid.UUID) -> MenuItem | None:
    return db.scalar(select(MenuItem).where(
        MenuItem.business_id == business_id, MenuItem.id == item_id))


def _apply_default_rule(item: MenuItem, variants: list[MenuItemVariant]) -> None:
    """Exactly one default: the first row ordered by sort_order then input order becomes
    the default when none is marked; two marked is an error (the import rule, F4)."""
    marked = [v for v in variants if v.is_default]
    if len(marked) > 1:
        raise MenuError("an item cannot have two default variants")
    if not marked:
        first = sorted(variants, key=lambda v: v.sort_order)[0]
        first.is_default = True


def create_item(db: Session, business_id: uuid.UUID, data: dict) -> MenuItem:
    variants_in = data.pop("variants")
    aliases_in = data.pop("aliases", [])
    ensure_category(db, business_id, data["category"])
    item = MenuItem(business_id=business_id, **data)
    variants = [MenuItemVariant(business_id=business_id, item_id=item.id, **v)
                for v in variants_in]
    _apply_default_rule(item, variants)
    item.variants = variants
    item.aliases = [MenuItemAlias(business_id=business_id, item_id=item.id, alias=a)
                    for a in aliases_in]
    db.add(item)
    db.flush()
    db.refresh(item)
    return item


def update_item(item: MenuItem, changes: dict) -> None:
    for key, value in changes.items():
        setattr(item, key, value)
    if item.sold_by == "weight" and not (item.unit_label or "").strip():
        raise MenuError("unit_label is required when sold_by is weight")


def set_availability(item: MenuItem, is_available: bool) -> None:
    item.is_available = is_available


def add_variant(db: Session, item: MenuItem, data: dict) -> MenuItemVariant:
    variant = MenuItemVariant(business_id=item.business_id, item_id=item.id, **data)
    if variant.is_default:
        _clear_default(db, item)
    db.add(variant)
    db.flush()
    # a first variant is always the default
    if len(item.variants) == 1 and not item.variants[0].is_default:
        variant.is_default = True
    return variant


def update_variant(db: Session, item: MenuItem, variant: MenuItemVariant,
                   changes: dict) -> None:
    for key, value in changes.items():
        setattr(variant, key, value)
    db.flush()


def _clear_default(db: Session, item: MenuItem) -> None:
    db.execute(
        update(MenuItemVariant).where(
            MenuItemVariant.business_id == item.business_id,
            MenuItemVariant.item_id == item.id,
            MenuItemVariant.is_default.is_(True),
        ).values(is_default=False)
    )


def set_default_variant(db: Session, item: MenuItem, variant: MenuItemVariant) -> None:
    _clear_default(db, item)
    variant.is_default = True
    db.flush()


def delete_variant(db: Session, item: MenuItem, variant: MenuItemVariant) -> None:
    if len(item.variants) <= 1:
        raise MenuError("an item must keep at least one variant")
    was_default = variant.is_default
    db.delete(variant)
    db.flush()
    if was_default:
        remaining = [v for v in item.variants if v.id != variant.id]
        if remaining:
            set_default_variant(db, item, sorted(remaining, key=lambda v: v.sort_order)[0])


def get_variant(db: Session, business_id: uuid.UUID, item_id: uuid.UUID,
                variant_id: uuid.UUID) -> MenuItemVariant | None:
    return db.scalar(select(MenuItemVariant).where(
        MenuItemVariant.business_id == business_id,
        MenuItemVariant.item_id == item_id,
        MenuItemVariant.id == variant_id))


def add_alias(db: Session, item: MenuItem, alias: str) -> MenuItemAlias:
    row = MenuItemAlias(business_id=item.business_id, item_id=item.id, alias=alias)
    db.add(row)
    db.flush()
    return row


def delete_alias(db: Session, business_id: uuid.UUID, item_id: uuid.UUID,
                 alias_id: uuid.UUID) -> bool:
    row = db.scalar(select(MenuItemAlias).where(
        MenuItemAlias.business_id == business_id,
        MenuItemAlias.item_id == item_id,
        MenuItemAlias.id == alias_id))
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def variant_price_changed(variant: MenuItemVariant, new_price: Decimal | None,
                          present: bool) -> bool:
    """Whether an update touches the price (PRICE_MANAGE gate in the route)."""
    return present and new_price != variant.price
