"""Menu CSV import (Phase 1, task 1.5, spec F4). Data-entry only: the bot keeps
reading `products` until the Phase 2 cutover (B2). No real menu data is imported in
Phase 1; this module is the machinery, validated by tests.

Rules (F4):
- CSV, UTF-8 (BOM accepted), header row required, comma separator.
- Required columns: category, item, variant, price, sold_by. Optional: unit_label,
  kind (food|service, default food), min_quantity, aliases (| separated), sort_order,
  description. Unknown column names are an error.
- price: number >= 0, at most 2 decimals; Arabic-Indic digits accepted; EMPTY price
  means NULL ("ask the cashier", B4). sold_by: unit|weight; weight needs unit_label.
- Natural key: (category, item, variant), normalized by trim, whitespace collapse and
  case-fold only - no Arabic letter folding in identity.
- Validation in full first; every failed row reported as row + column + reason.
  All or nothing: any failure writes nothing.
- dry_run (the dashboard default) reports would-create/update/unchanged.
- Idempotent: re-importing the same file reports 0 created, 0 updated, N unchanged;
  a changed price updates the variant with an audit row (old and new).
- is_default: the first row of each item, ordered by sort_order then file order,
  becomes the default when the item is created. Re-import keeps the current default.
- Each committed import writes one audit_events row with the file SHA-256 and counts.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.models_menu import MenuCategory, MenuItem, MenuItemAlias, MenuItemVariant

REQUIRED_COLUMNS = ["category", "item", "variant", "price", "sold_by"]
OPTIONAL_COLUMNS = ["unit_label", "kind", "min_quantity", "aliases", "sort_order",
                    "description"]
ALL_COLUMNS = REQUIRED_COLUMNS + OPTIONAL_COLUMNS
ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")
_WS = re.compile(r"\s+")


def _norm(value: str) -> str:
    """Natural-key normalization: trim, collapse whitespace, case-fold. No letter folding."""
    return _WS.sub(" ", value.strip()).casefold()


def _number(raw: str, *, allow_empty: bool) -> Decimal | None:
    """Parse a decimal, accepting Arabic-Indic digits; empty -> None when allowed."""
    raw = raw.strip().translate(ARABIC_DIGITS)
    if not raw:
        if allow_empty:
            return None
        raise ValueError("empty")
    value = Decimal(raw)
    if value < 0 or value.as_tuple().exponent < -2:
        raise ValueError("must be >= 0 with at most 2 decimals")
    return value


@dataclass
class RowError:
    row: int
    column: str
    reason: str


@dataclass
class ParsedRow:
    row: int
    category: str
    item: str
    variant: str
    price: Decimal | None
    sold_by: str
    unit_label: str | None
    kind: str
    min_quantity: Decimal | None
    aliases: list[str] = field(default_factory=list)
    sort_order: int = 0
    description: str | None = None

    @property
    def item_key(self) -> tuple[str, str]:
        return (_norm(self.category), _norm(self.item))

    @property
    def natural_key(self) -> tuple[str, str, str]:
        return (*self.item_key, _norm(self.variant))


def parse_menu_csv(content: bytes) -> tuple[list[ParsedRow], list[RowError]]:
    """Validate the whole file. Returns (rows, errors); rows are usable only if
    errors is empty."""
    text = content.decode("utf-8-sig")  # utf-8-sig accepts an Excel BOM
    reader = csv.DictReader(io.StringIO(text))
    errors: list[RowError] = []
    if reader.fieldnames is None:
        return [], [RowError(1, "(header)", "missing header row")]
    unknown = [c for c in reader.fieldnames if c and c.strip() not in ALL_COLUMNS]
    for col in unknown:
        errors.append(RowError(1, col, "unknown column"))
    missing = [c for c in REQUIRED_COLUMNS if c not in (reader.fieldnames or [])]
    for col in missing:
        errors.append(RowError(1, col, "missing required column"))
    if errors:
        return [], errors

    rows: list[ParsedRow] = []
    for n, raw in enumerate(reader, start=2):  # row 1 is the header
        def cell(col: str, row: dict = raw) -> str:
            return (row.get(col) or "").strip()

        row_errors: list[RowError] = []
        category, item, variant = cell("category"), cell("item"), cell("variant")
        for col, val in (("category", category), ("item", item), ("variant", variant)):
            if not val:
                row_errors.append(RowError(n, col, "must not be empty"))
        sold_by = cell("sold_by")
        if sold_by not in ("unit", "weight"):
            row_errors.append(RowError(n, "sold_by", "must be unit or weight"))
        kind = cell("kind") or "food"
        if kind not in ("food", "service"):
            row_errors.append(RowError(n, "kind", "must be food or service"))
        unit_label = cell("unit_label") or None
        if sold_by == "weight" and not unit_label:
            row_errors.append(RowError(n, "unit_label", "required when sold_by=weight"))
        try:
            price = _number(cell("price"), allow_empty=True)
        except (InvalidOperation, ValueError):
            price = None
            row_errors.append(RowError(n, "price", "number >= 0, max 2 decimals, or empty"))
        try:
            min_q = _number(cell("min_quantity"), allow_empty=True)
        except (InvalidOperation, ValueError):
            min_q = None
            row_errors.append(RowError(n, "min_quantity", "number >= 0 or empty"))
        try:
            sort_order = int(cell("sort_order").translate(ARABIC_DIGITS)) \
                if cell("sort_order") else 0
        except ValueError:
            sort_order = 0
            row_errors.append(RowError(n, "sort_order", "must be an integer"))
        errors.extend(row_errors)
        if row_errors:
            continue
        rows.append(ParsedRow(
            row=n, category=category, item=item, variant=variant, price=price,
            sold_by=sold_by, unit_label=unit_label, kind=kind, min_quantity=min_q,
            aliases=[a.strip() for a in cell("aliases").split("|") if a.strip()],
            sort_order=sort_order, description=cell("description") or None))

    seen_keys: dict[tuple[str, str, str], int] = {}
    item_sold_by: dict[tuple[str, str], tuple[str, int]] = {}
    for r in rows:
        if r.natural_key in seen_keys:
            errors.append(RowError(r.row, "variant",
                                   f"duplicate of row {seen_keys[r.natural_key]}"))
        else:
            seen_keys[r.natural_key] = r.row
        prev = item_sold_by.get(r.item_key)
        if prev and prev[0] != r.sold_by:
            errors.append(RowError(r.row, "sold_by",
                                   f"conflicts with row {prev[1]} for the same item"))
        else:
            item_sold_by.setdefault(r.item_key, (r.sold_by, r.row))
    return rows, errors


@dataclass
class ImportResult:
    dry_run: bool
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: list[RowError] = field(default_factory=list)
    sha256: str = ""

    def as_dict(self) -> dict:
        return {"dry_run": self.dry_run, "created": self.created,
                "updated": self.updated, "unchanged": self.unchanged,
                "sha256": self.sha256,
                "errors": [{"row": e.row, "column": e.column, "reason": e.reason}
                           for e in self.errors]}


def import_menu_csv(db: Session, business_id: uuid.UUID, content: bytes, *,
                    dry_run: bool = True,
                    actor_id: uuid.UUID | None = None) -> ImportResult:
    """Validate, then apply (or preview) the file. All or nothing: any row error
    writes nothing."""
    result = ImportResult(dry_run=dry_run,
                          sha256=hashlib.sha256(content).hexdigest())
    rows, errors = parse_menu_csv(content)
    result.errors = errors
    if errors:
        return result

    # group rows per item, keeping file order inside each item
    items_in_order: list[tuple[str, str]] = []
    by_item: dict[tuple[str, str], list[ParsedRow]] = {}
    for r in rows:
        if r.item_key not in by_item:
            by_item[r.item_key] = []
            items_in_order.append(r.item_key)
        by_item[r.item_key].append(r)

    existing = {(_norm(i.category), _norm(i.name)): i for i in db.scalars(
        select(MenuItem).where(MenuItem.business_id == business_id))}
    changes: list[dict] = []
    for key in items_in_order:
        group = by_item[key]
        first = group[0]
        item = existing.get(key)
        if item is None:
            category_row = db.scalar(select(MenuCategory).where(
                MenuCategory.business_id == business_id,
                MenuCategory.name == first.category))
            if category_row is None:
                db.add(MenuCategory(business_id=business_id, name=first.category))
                db.flush()  # the item FK references (business_id, category)
            item = MenuItem(business_id=business_id, category=first.category,
                            name=first.item, kind=first.kind, sold_by=first.sold_by,
                            unit_label=first.unit_label, min_quantity=first.min_quantity,
                            description=first.description, sort_order=first.sort_order)
            db.add(item)
            db.flush()
            variants = sorted(group, key=lambda r: (r.sort_order, r.row))
            for pos, r in enumerate(variants):
                db.add(MenuItemVariant(
                    business_id=business_id, item_id=item.id, name=r.variant,
                    price=r.price, is_default=(pos == 0), sort_order=r.sort_order))
                result.created += 1
                changes.append({"action": "create", "row": r.row,
                                "item": r.item, "variant": r.variant})
            seen_aliases: set[str] = set()
            for r in group:
                for alias in r.aliases:
                    if _norm(alias) not in seen_aliases:
                        db.add(MenuItemAlias(business_id=business_id, item_id=item.id,
                                             alias=alias))
                        seen_aliases.add(_norm(alias))
        else:
            current = {(_norm(v.name)): v for v in db.scalars(
                select(MenuItemVariant).where(MenuItemVariant.item_id == item.id))}
            for r in group:
                variant = current.get(_norm(r.variant))
                if variant is None:
                    db.add(MenuItemVariant(business_id=business_id, item_id=item.id,
                                           name=r.variant, price=r.price,
                                           sort_order=r.sort_order))
                    result.created += 1
                    changes.append({"action": "create", "row": r.row, "item": r.item,
                                    "variant": r.variant})
                elif variant.price != r.price:
                    changes.append({"action": "update", "row": r.row, "item": r.item,
                                    "variant": r.variant,
                                    "old": str(variant.price), "new": str(r.price)})
                    if not dry_run:
                        variant.price = r.price
                    result.updated += 1
                else:
                    result.unchanged += 1
    if dry_run:
        db.rollback()
        return result
    write_audit(db, business_id=business_id, actor_id=actor_id, entity="menu_import",
                entity_id=result.sha256[:16], action="import",
                payload={"sha256": result.sha256, "created": result.created,
                         "updated": result.updated, "unchanged": result.unchanged,
                         "changes": changes})
    db.commit()
    return result
