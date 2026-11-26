"""Arabic matching accuracy sweep over the imported menu (Task 1.8).

Runs every menu item through the catalog matcher under the phrasings a customer
would actually type (exact, ة/ه swap, ى/ي swap, no ال, first word(s), with an
Eastern-digit quantity) and reports hit/miss rates per category plus every miss.

Usage (menu must already be imported):
    DATABASE_URL=... python scripts/menu_match_accuracy.py --business beit-el-lahma
"""

import argparse
import re
import sys

from sqlalchemy import select

sys.path.insert(0, ".")
from app.chat.menu_catalog import load_catalog  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.models import Business  # noqa: E402
from app.models_menu import MenuItem  # noqa: E402

AL_PREFIX = re.compile(r"^ال")


def phrasings(name: str) -> list[tuple[str, str]]:
    out = [("exact", name)]
    if "ة" in name:
        out.append(("taa->haa", name.replace("ة", "ه")))
    if "ى" in name:
        out.append(("yaa->aa", name.replace("ى", "ي")))
    stripped = " ".join(AL_PREFIX.sub("", w) for w in name.split())
    if stripped != name:
        out.append(("no-al", stripped))
    words = name.split()
    if len(words) > 1:
        out.append(("first-word", words[0]))
    if len(words) > 2:
        out.append(("first-two", " ".join(words[:2])))
    out.append(("with-qty", "٢ " + name))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--business", required=True)
    args = ap.parse_args()
    db = SessionLocal()
    try:
        biz = db.scalar(select(Business).where(Business.slug == args.business))
        cat = load_catalog(db, biz.id)
        items = db.scalars(select(MenuItem).where(
            MenuItem.business_id == biz.id, MenuItem.is_available.is_(True))).all()
        rows = []
        for item in items:
            iid = str(item.id)
            for kind, text in phrasings(item.name):
                hits = set()
                for r in cat.matcher.match_text(text):
                    if r.product_id:
                        hits.add(str(r.product_id))
                    for c in (r.candidates or []):
                        hits.add(str(c.product_id))
                rows.append((item.category, item.name, kind, text, iid in hits))
        cats = {}
        for category, _, _, _, ok in rows:
            a, b = cats.get(category, (0, 0))
            cats[category] = (a + ok, b + 1)
        total_ok = sum(ok for *_, ok in rows)
        print(f"TOTAL: {total_ok}/{len(rows)} ({100 * total_ok / len(rows):.1f}%)")
        for category, (a, b) in cats.items():
            print(f"  {category}: {a}/{b} ({100 * a / b:.1f}%)")
        misses = [r for r in rows if not r[4]]
        print(f"\nMISSES ({len(misses)}):")
        for category, name, kind, text, _ in misses:
            print(f"  [{category}] {name!r} phrasing {kind}: {text!r}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
