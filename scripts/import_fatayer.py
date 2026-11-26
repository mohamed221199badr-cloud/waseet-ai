"""Import the الفطائر category (scripts/fatayer_data.py) for one business.

Usage:
    python scripts/import_fatayer.py --business <slug-or-uuid>            # dry run
    python scripts/import_fatayer.py --business <slug-or-uuid> --commit

Touches ONLY the category "الفطائر" (created at the end of the category order if
missing). Existing categories, items and photos are never changed. Re-running
upserts by (category, item name) and (item, variant name); it never deletes.
Variant names are the printed sizes (M/L/XL/XXL, weights, ...). One audit row.
"""

import argparse
import sys
import uuid
from decimal import Decimal

from sqlalchemy import func, select

sys.path.insert(0, ".")
sys.path.insert(0, "scripts")
from fatayer_data import ITEMS, PREP  # noqa: E402

from app.audit import write_audit  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Business  # noqa: E402
from app.models_menu import MenuCategory, MenuItem, MenuItemVariant  # noqa: E402

CATEGORY = "الفطائر"
# left out until the owner confirms (WhatsApp 9:54 go: only confident rows)
SKIP_SECTIONS = {PREP}  # prep services: not asked for
SKIP_NAMES = {"فطيرة لحمة مفرومة معصج"}  # name unclear in the brochure


def run(db, business_id, commit: bool) -> dict:
    cat = db.scalar(select(MenuCategory).where(
        MenuCategory.business_id == business_id, MenuCategory.name == CATEGORY))
    if cat is None:
        top = db.scalar(select(func.max(MenuCategory.sort_order)).where(
            MenuCategory.business_id == business_id)) or 0
        cat = MenuCategory(business_id=business_id, name=CATEGORY, sort_order=top + 1)
        db.add(cat)
        db.flush()
    items = variants = 0
    for sort, (_section, name, pairs, _note) in enumerate(ITEMS):
        if _section in SKIP_SECTIONS or name in SKIP_NAMES:
            continue
        kind = "service" if _section == PREP else "food"
        row = db.scalar(select(MenuItem).where(
            MenuItem.business_id == business_id, MenuItem.category == CATEGORY,
            MenuItem.name == name))
        if row is None:
            row = MenuItem(business_id=business_id, category=CATEGORY, name=name,
                           kind=kind, sold_by="unit", sort_order=sort)
            db.add(row)
            db.flush()
        else:
            row.kind, row.sort_order, row.is_available = kind, sort, True
        items += 1
        for vsort, (vname, price) in enumerate(pairs):
            v = db.scalar(select(MenuItemVariant).where(
                MenuItemVariant.business_id == business_id,
                MenuItemVariant.item_id == row.id, MenuItemVariant.name == vname))
            if v is None:
                db.add(MenuItemVariant(
                    business_id=business_id, item_id=row.id, name=vname,
                    price=Decimal(str(price)), is_default=vsort == 0, sort_order=vsort))
            else:
                v.price, v.is_default, v.sort_order = Decimal(str(price)), vsort == 0, vsort
            variants += 1
    stats = {"items": items, "variants": variants}
    if commit:
        write_audit(db, business_id=business_id, entity="menu_category",
                    entity_id=str(cat.id), action="import",
                    payload={"by": "seed_by_agent", "category": CATEGORY, **stats})
        db.commit()
    else:
        db.rollback()
    return stats


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--business", required=True)
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()
    db = SessionLocal()
    try:
        cond = Business.slug == args.business
        try:
            cond = cond | (Business.id == uuid.UUID(args.business))
        except ValueError:
            pass
        biz = db.scalar(select(Business).where(cond))
        if biz is None:
            sys.exit(f"business not found: {args.business}")
        stats = run(db, biz.id, args.commit)
        print(("COMMITTED" if args.commit else "DRY RUN (rolled back)"), stats, biz.slug)
    finally:
        db.close()


if __name__ == "__main__":
    main()
