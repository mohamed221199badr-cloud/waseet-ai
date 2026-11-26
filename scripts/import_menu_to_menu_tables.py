"""Import the owner's raw menu text into the m007 menu tables (idempotent).

Usage:
    python scripts/import_menu_to_menu_tables.py --business <slug-or-id> --file menu.txt [--dry-run]

Input format (as supplied by the owner):
    [CATEGORY: اللحوم] (Sold by: weight)
    - مفروم احمر (450)
    - برجر بلدي (280 / 350 / 450)
    - كبيبة شامي (NULL) -> ask cashier

Rules:
- Categories marked "(Sold by: weight)" import as sold_by=weight, unit_label=كيلو;
  everything else imports as sold_by=unit. "(kind: service)" marks the category's
  items as kind=service.
- One price -> one variant. Several prices -> variants named سعر 1, سعر 2, سعر 3
  (placeholders until the owner confirms real names like ربع/نص/كيلو). The first
  variant is the default.
- NULL price -> a variant with price NULL: the bot never quotes it (B4), the
  cashier prices it at confirm time.
- Re-running upserts by natural keys and refreshes prices; it never deletes rows
  that disappeared from the file (the owner removes items in the UI).
"""

import argparse
import re
import sys
import uuid
from decimal import Decimal

from sqlalchemy import select

sys.path.insert(0, ".")
from app.db import SessionLocal  # noqa: E402
from app.models import Business  # noqa: E402
from app.models_menu import (  # noqa: E402
    MenuCategory,
    MenuItem,
    MenuItemAlias,
    MenuItemVariant,
)

VARIANT_PLACEHOLDER = "سعر {n}"
ITEM_RE = re.compile(r"^-\s*(?P<name>.+?)\s*\((?P<prices>[0-9NULL\s/]+)\)\s*(->.*)?$")
CATEGORY_RE = re.compile(r"^\[CATEGORY:\s*(?P<name>.+?)\]\s*(?P<flags>.*)$")


def parse_menu(text: str) -> list[dict]:
    """Return [{name, weight, service, items: [{name, prices: [Decimal|None]}]}]."""
    categories: list[dict] = []
    current = None
    for raw in text.splitlines():
        line = raw.strip()
        cat = CATEGORY_RE.match(line)
        if cat:
            flags = cat.group("flags").lower()
            current = {
                "name": cat.group("name").strip(),
                "weight": "weight" in flags,
                "service": "service" in flags,
                "items": [],
            }
            categories.append(current)
            continue
        item = ITEM_RE.match(line)
        if item and current is not None:
            prices = []
            for part in item.group("prices").split("/"):
                part = part.strip()
                prices.append(None if part.upper() == "NULL" else Decimal(part))
            current["items"].append({"name": item.group("name").strip(), "prices": prices})
    return categories


def import_menu(db, business_id: uuid.UUID, categories: list[dict], dry_run: bool) -> dict:
    stats = {"categories": 0, "items": 0, "variants": 0}
    for sort, cat in enumerate(categories):
        row = db.scalar(select(MenuCategory).where(
            MenuCategory.business_id == business_id, MenuCategory.name == cat["name"]))
        if row is None:
            row = MenuCategory(business_id=business_id, name=cat["name"], sort_order=sort)
            db.add(row)
            db.flush()
        else:
            row.sort_order = sort
        stats["categories"] += 1
        for isort, item in enumerate(cat["items"]):
            mrow = db.scalar(select(MenuItem).where(
                MenuItem.business_id == business_id,
                MenuItem.category == cat["name"], MenuItem.name == item["name"]))
            if mrow is None:
                mrow = MenuItem(
                    business_id=business_id, category=cat["name"], name=item["name"],
                    kind="service" if cat["service"] else "food",
                    sold_by="weight" if cat["weight"] else "unit",
                    unit_label="كيلو" if cat["weight"] else None,
                    sort_order=isort)
                db.add(mrow)
                db.flush()
            else:
                mrow.kind = "service" if cat["service"] else "food"
                mrow.sold_by = "weight" if cat["weight"] else "unit"
                mrow.unit_label = "كيلو" if cat["weight"] else None
                mrow.sort_order = isort
                mrow.is_available = True
            stats["items"] += 1
            for vsort, price in enumerate(item["prices"]):
                vname = VARIANT_PLACEHOLDER.format(n=vsort + 1)
                vrow = db.scalar(select(MenuItemVariant).where(
                    MenuItemVariant.business_id == business_id,
                    MenuItemVariant.item_id == mrow.id,
                    MenuItemVariant.name == vname))
                if vrow is None:
                    vrow = MenuItemVariant(
                        business_id=business_id, item_id=mrow.id, name=vname,
                        price=price, is_default=vsort == 0, sort_order=vsort)
                    db.add(vrow)
                    db.flush()
                else:
                    vrow.price = price
                    vrow.is_default = vsort == 0
                    vrow.sort_order = vsort
                stats["variants"] += 1
            # drop surplus placeholder variants if the price list shrank
            keep = {VARIANT_PLACEHOLDER.format(n=i + 1) for i in range(len(item["prices"]))}
            for old in db.scalars(select(MenuItemVariant).where(
                    MenuItemVariant.business_id == business_id,
                    MenuItemVariant.item_id == mrow.id)).all():
                if old.name not in keep and old.name.startswith("سعر "):
                    db.delete(old)
    if dry_run:
        db.rollback()
    else:
        db.commit()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--business", required=True, help="business slug or UUID")
    ap.add_argument("--file", required=True, help="raw menu text file")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    with open(args.file, encoding="utf-8") as f:
        categories = parse_menu(f.read())
    if not categories:
        print("no categories parsed; check the file format", file=sys.stderr)
        sys.exit(1)
    db = SessionLocal()
    try:
        biz = db.scalar(select(Business).where(
            (Business.slug == args.business) | (Business.id == args.business
                                                if _is_uuid(args.business) else False)))
        if biz is None:
            print(f"business not found: {args.business}", file=sys.stderr)
            sys.exit(1)
        stats = import_menu(db, biz.id, categories, dry_run=args.dry_run)
        alias_count = db.scalar(select(MenuItemAlias).where(
            MenuItemAlias.business_id == biz.id).limit(1))
        mode = "DRY RUN (rolled back)" if args.dry_run else "COMMITTED"
        print(f"{mode}: {stats['categories']} categories, {stats['items']} items, "
              f"{stats['variants']} variants into business {biz.slug} ({biz.id})"
              + ("; aliases untouched" if alias_count is not None else ""))
    finally:
        db.close()


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except ValueError:
        return False


if __name__ == "__main__":
    main()
