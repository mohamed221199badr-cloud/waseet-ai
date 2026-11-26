#!/usr/bin/env python
"""Bulk-import item photos: one file per item, filename = exact item name.

    python -m scripts.import_item_images --business-id <uuid> --dir <dir> [--apply]

Filenames are matched to item names after the same normalization the chatbot
matcher uses. Ambiguous or unmatched filenames are REPORTED, never guessed.
Dry-run by default; --apply writes.
"""

import argparse
import os
import sys
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.models  # noqa: E402,F401  (registers every table for mapper config)
from app.matching.normalize import normalize as normalize_arabic  # noqa: E402
from app.models_menu import MenuItem, MenuItemImage  # noqa: E402

TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def run(business_id: str, directory: str, apply: bool, session=None) -> int:
    ok = ambiguous = unmatched = 0
    own = session is None
    engine = None if session is not None else create_engine(os.environ["DATABASE_URL"])
    import contextlib
    cm = contextlib.nullcontext(session) if not own else Session(engine)
    with cm as db:
        items = list(db.execute(
            select(MenuItem).where(MenuItem.business_id == uuid.UUID(business_id))).scalars())
        by_name = {}
        for item in items:
            by_name.setdefault(normalize_arabic(item.name), []).append(item)
        for fname in sorted(os.listdir(directory)):
            stem, ext = os.path.splitext(fname)
            ctype = TYPES.get(ext.lower())
            if ctype is None:
                print(f"SKIP {fname}: unsupported extension")
                continue
            key = normalize_arabic(stem)
            matches = by_name.get(key, [])
            if not matches:
                unmatched += 1
                print(f"NO-ITEM {fname}")
                continue
            if len(matches) > 1:
                ambiguous += 1
                cats = ", ".join(sorted({m.category for m in matches}))
                print(f"AMBIGUOUS {fname}: matches {len(matches)} items ({cats}) - rename with the category")
                continue
            item = matches[0]
            with open(os.path.join(directory, fname), "rb") as fh:
                data = fh.read()
            if len(data) > 5 * 1024 * 1024:
                print(f"SKIP {fname}: larger than 5MB")
                continue
            if apply:
                row = db.execute(select(MenuItemImage).where(
                    MenuItemImage.business_id == item.business_id,
                    MenuItemImage.item_id == item.id)).scalar_one_or_none()
                if row is None:
                    db.add(MenuItemImage(business_id=item.business_id, item_id=item.id,
                                         image=data, content_type=ctype))
                else:
                    row.image, row.content_type, row.telegram_file_id = data, ctype, None
            ok += 1
            print(f"OK {fname} -> {item.name} [{item.category}] ({len(data)} bytes)")
        if apply:
            db.commit() if own else db.flush()
    print(f"done: {ok} {'imported' if apply else 'would import'}, {ambiguous} ambiguous, {unmatched} unmatched")
    return 0 if (ambiguous == 0 and unmatched == 0) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--business-id", required=True)
    ap.add_argument("--dir", required=True)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    raise SystemExit(run(args.business_id, args.dir, args.apply))
