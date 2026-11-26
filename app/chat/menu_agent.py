"""Phase 2 cutover: the rules chat agent running on the restaurant menu tables.

Same conversation shape and same Egyptian-Arabic voice as the legacy products agent
(app/chat/agent.py), with two differences the menu makes possible:
- items can have several priced variants, so one more question kind exists: need_variant
  (item picked -> variant buttons/list with DB prices -> quantity). Quantities are still
  never guessed, and weight-sold items accept fractions (2.5 كيلو).
- prices resolve per B4 through PriceService rules (customer override -> menu base price);
  a NULL price with no override is never quoted and the line never enters the draft
  (the cashier prices it), matching the approved trial behavior.

No LLM here: the cutover keeps the proven rules engine; the LLM agent is a later phase.
A business enters this mode only when menu_catalog.has_menu is true; anything else stays
on the legacy products path unchanged.
"""

import random
import re
import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.audit import write_audit
from app.chat import agent as legacy
from app.chat.menu_catalog import (
    Catalog,
    category_items,
    has_menu,
    list_categories,
    load_catalog,
    resolve_variant_price,
)
from app.config import settings
from app.matching.engine import CatalogEntry, Matcher
from app.matching.normalize import normalize, normalize_digits
from app.matching.parser import parse_line
from app.models_menu import MenuItem, MenuItemVariant
from app.models_orders import (
    ChatSession,
    Customer,
    Order,
    OrderException,
    OrderLine,
    TelegramLink,
)
from app.repositories import orders as orders_repo

MAX_BUTTONS = 10


def _money(d) -> str:
    return str(Decimal(d).quantize(Decimal("0.01")))


_GENERIC_VARIANT = re.compile(r"^\s*سعر\s*[0-9٠-٩]+\s*$")


def is_generic_variant(name: str) -> bool:
    """«سعر 1» / «سعر ٢»: a price tier label, not a real size or weight name."""
    return bool(_GENERIC_VARIANT.match(name or ""))


def _price_short(d) -> str:
    d = Decimal(d)
    return str(int(d)) if d == d.to_integral_value() else _money(d)


def variant_label(name: str, price) -> str:
    """Display only: a generic «سعر N» variant shows its actual unit price (270ج); real
    names (M, L, XL, weights) stay as they are. The DB name is never changed."""
    if price is not None and is_generic_variant(name):
        return f"{_price_short(price)}ج"
    return name

MSG_VARIANT = "«{item}» عندنا بأكتر من سعر، تحب أنهي؟"
MSG_PRICE_REQUIRED = ("«{item}» سعره بيتحدد مع الكاشير عشان الوزن، فمش هقدر أضيفه للطلب "
                      "من هنا. الكاشير هيكلمك فيه، تحب تضيف حاجة تانية؟")
MSG_QTY_WEIGHT = "كام «{name}» عايز؟ اكتب الكمية بالرقم (مثال: 2 أو 2.5 {unit})."


def menu_mode(db: Session, link: TelegramLink) -> bool:
    return has_menu(db, link.business_id)


def handle_message(db: Session, link: TelegramLink, text: str) -> str:
    """One customer message -> one reply, on the menu catalog. The caller commits."""
    if len(text) > legacy.MAX_TEXT:
        return "الرسالة طويلة جدًا. قسّمها لرسائل أقصر."
    customer = db.scalar(select(Customer).where(
        Customer.business_id == link.business_id, Customer.id == link.customer_id))
    if customer is None or not customer.is_active:
        return "الحساب ده متوقف عند المحل. كلّم المحل مباشرة."
    session = legacy._load(db, link)
    lines: list[dict] = []
    state = "collecting"
    if session is not None:
        if session.draft.get("kind") != "menu":
            legacy._clear(db, session)  # stale legacy draft: start clean, never mix modes
            session = None
        else:
            lines = session.draft.get("lines", [])
            state = session.state
    cat = load_catalog(db, link.business_id)

    if session is not None and legacy._is(text, legacy.CANCEL):
        legacy._clear(db, session)
        return legacy.MSG_CANCELLED

    if state == "clarifying":
        line = next((ln for ln in lines
                     if ln["status"] in ("need_qty", "need_choice", "need_variant")), None)
        if line is not None:
            ok = _apply_answer(db, link, cat, line, text)
            if not ok:
                chat = _smalltalk(db, link, text, cat)
                if chat is not None:
                    _save_menu(db, link, session, "clarifying", lines)
                    return chat + "\n\n" + (_next_question(db, link, cat, lines) or "")
                extra = _lines_from_text(db, link, cat, text)
                if any(x["status"] != "unresolved" for x in extra):
                    have = {ln["item_id"] for ln in lines if ln.get("item_id")}
                    for x in extra:
                        if x["status"] == "ok" and x["item_id"] in have:
                            x.update(status="unresolved", reason="duplicate_item")
                    lines += extra
                    return _advance(db, link, session, cat, lines)
                line["asked"] = line.get("asked", 0) + 1
                if line["asked"] >= 3:  # stop nagging: the owner settles this line
                    line.update(status="unresolved", reason="customer_unclear")
                else:
                    _save_menu(db, link, session, "clarifying", lines)
                    return legacy.MSG_REASK_QTY if line["status"] == "need_qty" else (
                        legacy.MSG_REASK_CHOICE + "\n"
                        + (_next_question(db, link, cat, lines) or ""))
            return _advance(db, link, session, cat, lines)

    if state == "confirming":
        label = ("yes" if legacy._is(text, legacy.YES)
                 else "no" if legacy._is(text, legacy.NO) else None)
        if label == "yes":
            # a typed «تمام» starts the same checkout the cart button does:
            # phone/delivery/address are collected before the order exists
            from app.chat import checkout
            checkout.begin(db, link)
            return ""  # the caller renders the checkout step
        if label == "no":
            legacy._clear(db, session)
            return legacy.MSG_CANCELLED
        chat = _smalltalk(db, link, text, cat)
        if chat is not None:
            _save_menu(db, link, session, "confirming", lines)
            return chat + "\n\n" + legacy.MSG_REASK_CONFIRM
        extra = _lines_from_text(db, link, cat, text)
        if extra:
            lines += extra
            return _advance(db, link, session, cat, lines)
        return legacy.MSG_REASK_CONFIRM

    # collecting (no open question)
    chat = _smalltalk(db, link, text, cat)
    if chat is not None:
        return chat
    lines += _lines_from_text(db, link, cat, text)
    lines = _real_lines(lines)
    if not lines:
        return legacy.MSG_DONT_GET
    return _advance(db, link, session, cat, lines)


# ---- session ---------------------------------------------------------------------------
def _save_menu(db: Session, link: TelegramLink, session, state: str, lines: list[dict]) -> None:
    if session is None:
        session = ChatSession(business_id=link.business_id, customer_id=link.customer_id,
                              chat_id=link.chat_id)
        db.add(session)
    session.state = state
    session.draft = {"kind": "menu", "lines": lines}
    # lines dicts mutate in place between saves; the new top-level dict still ==
    # the loaded one (same list object), so SQLAlchemy suppresses the change.
    flag_modified(session, "draft")
    session.updated_at = legacy._now()
    db.flush()


# ---- text -> lines ---------------------------------------------------------------------
_CONTROL_WORDS = (legacy.YES | legacy.NO | legacy.CANCEL | legacy.SKIP
                  | legacy.GREET | legacy.THANKS | legacy.HELP_Q)


def _lines_from_text(db: Session, link: TelegramLink, cat: Catalog, text: str) -> list[dict]:
    # conversation control words ("تمام", "شكرا", "الغي", ...) are never order
    # lines: the fuzzy matcher can offer junk candidates for them (تمام -> التوماموك)
    if legacy._is(text, _CONTROL_WORDS):
        return []
    out: list[dict] = []
    seen: set[str] = set()
    for r in cat.matcher.match_text(text):
        line = {"raw": r.raw[:200], "qty": r.quantity, "item_id": None, "variant_id": None,
                "candidates": [], "status": "unresolved",
                "reason": r.reason or "not_found", "asked": 0}
        iid = str(r.product_id) if r.product_id else None
        if r.status == "matched" and iid not in seen:
            seen.add(iid)
            line["item_id"] = iid
            _route_matched(db, link, cat, line)
        elif r.status == "matched":
            line["reason"] = "duplicate_item"
        elif r.reason in ("missing_quantity", "unclear_quantity") and iid:
            line["item_id"] = iid
            _weight_qty_from_size(line, cat, r.raw)
            _route_matched(db, link, cat, line, qty_known=bool(line["qty"]))
        elif r.reason in ("ambiguous", "low_confidence") and r.candidates:
            # the matcher can list the SAME item twice (name + alias hit): that is no
            # ambiguity - dedupe, and a single survivor routes like a plain match.
            # a low-confidence hit is always confirmed, never silently accepted
            # ("توم" -> التوماموك is a guess, not a match).
            cands = list(dict.fromkeys(str(c.product_id) for c in r.candidates))
            if len(cands) == 1 and r.reason == "ambiguous":
                line["item_id"] = cands[0]
                _weight_qty_from_size(line, cat, r.raw)
                _route_matched(db, link, cat, line, qty_known=bool(line["qty"]))
            else:
                line.update(candidates=cands, status="need_choice")
        out.append(line)
    return out


def _weight_qty_from_size(line: dict, cat: Catalog, raw: str) -> None:
    """"كيلو مفروم" / "نص كيلو مفروم": the parser reads the unit as a mass Size,
    not a quantity. For weight items that IS the quantity (grams / 1000 = كيلو)."""
    if line["qty"] or not line.get("item_id"):
        return
    item = cat.items.get(line["item_id"])
    if item is None or item.sold_by != "weight":
        return
    parsed = parse_line(raw)
    if parsed.size is not None and parsed.size.dimension == "mass":
        line["qty"] = float(parsed.size.amount / 1000)  # JSONB-safe; re-decimalized later


def _route_matched(db: Session, link: TelegramLink, cat: Catalog, line: dict,
                   qty_known: bool = True) -> None:
    """An item is matched: resolve its variant situation and price before it can be ok."""
    vs = cat.variants.get(line["item_id"], [])
    if not vs:
        line.update(status="unresolved", reason="no_variants")
        return
    if len(vs) > 1:
        line["status"] = "need_variant"
        return
    line["variant_id"] = str(vs[0].id)
    if not _price_ok(db, link, cat, line):
        return
    line["status"] = "ok" if (qty_known and line["qty"]) else "need_qty"


def _price_ok(db: Session, link: TelegramLink, cat: Catalog, line: dict) -> bool:
    """B4 gate: NULL price with no override never enters a draft (cashier prices it)."""
    v = cat.variant(line["item_id"], line["variant_id"])
    price, source = resolve_variant_price(db, link.business_id, link.customer_id, v)
    if price is None:
        line.update(status="unresolved", reason="price_required")
        return False
    line["unit_price"] = str(price)
    line["price_source"] = source
    return True


def _real_lines(lines: list[dict]) -> list[dict]:
    return [x for x in lines if not (x["status"] == "unresolved" and not x["qty"])
            or x["reason"] == "price_required"]


# ---- answers to open questions -----------------------------------------------------------
def _apply_answer(db: Session, link: TelegramLink, cat: Catalog, line: dict, text: str) -> bool:
    if line["status"] == "need_qty":
        return _apply_qty(line, text, cat.items.get(line["item_id"]))
    if line["status"] == "need_variant":
        return _apply_variant(db, link, cat, line, text)
    return _apply_choice(db, link, cat, line, text)


_FRAC_WORDS = {"نص": Decimal("0.5"), "نصين": Decimal("1"), "ربع": Decimal("0.25"),
               "تلت": Decimal("1") / Decimal(3), "اتنين": Decimal("2"), "واحد": Decimal("1"),
               "ثلاثه": Decimal("3"), "اربعه": Decimal("4"), "خمسه": Decimal("5")}
_UNIT_WORDS = {"كيلو", "كيلوجرام", "كيلوغرام", "جرام", "ك", "كج", "و", "ونص"}


def _apply_qty(line: dict, text: str, item: MenuItem | None = None) -> bool:
    t = normalize_digits(text.strip())
    parsed = parse_line("صنف " + t)
    qty = parsed.quantity if parsed.key == "صنف" else None
    if qty is None and item is not None and item.sold_by == "weight":
        # weight items accept fractions the int parser cannot: "2.5", "٢,٥", "نص", "2 ونص"
        words = [w for w in re.split(r"[\s،]+", t) if w and w not in _UNIT_WORDS]
        # bare unit as the whole answer: "كيلو" to "كام؟" means 1 كيلو
        if not words and any(w in ("كيلو", "كيلوجرام", "كيلوغرام")
                             for w in re.split(r"[\s،]+", t)):
            qty = 1.0
        total = Decimal("0")
        for w in words:
            w = w.replace("٫", ".").replace(",", ".")
            if w in _FRAC_WORDS:
                total += _FRAC_WORDS[w]
            else:
                try:
                    total += Decimal(w)
                except ArithmeticError:
                    return False
        if words:
            qty = float(total)   # JSONB-safe; money math re-decimalizes via str()
    if qty is None or qty <= 0 or qty > 100000:
        return False
    line.update(qty=qty, status="ok")
    return True


def _apply_variant(db: Session, link: TelegramLink, cat: Catalog, line: dict, text: str) -> bool:
    t = normalize_digits(text.strip())
    if legacy._is(t, legacy.SKIP):
        line.update(status="unresolved", reason="customer_skipped")
        return True
    vs = cat.variants.get(line["item_id"], [])
    pick: MenuItemVariant | None = None
    if t.isdigit() and 1 <= int(t) <= len(vs):
        pick = vs[int(t) - 1]
    elif not t.isdigit():
        # typed variant name ("سعر 3", later "ربع"/"نص"/"كيلو"): the name itself can
        # carry digits, so the order parser would misread them as a quantity.
        # Match on the normalized name instead of routing through parse_line.
        norm = normalize(t)
        exact = [v for v in vs if normalize(v.name) == norm]
        inside = [v for v in vs if normalize(v.name) in norm or norm in normalize(v.name)]
        uniq = {v.id: v for v in (exact or inside)}
        if len(uniq) == 1:
            pick = next(iter(uniq.values()))
    if pick is None:
        return False
    line["variant_id"] = str(pick.id)
    if not _price_ok(db, link, cat, line):
        return True   # price_required is a settled answer: the cashier handles this line
    if line["qty"] and line["qty"] > 0:
        line["status"] = "ok"
    else:
        line["status"] = "need_qty"
    return True


def _apply_choice(db: Session, link: TelegramLink, cat: Catalog, line: dict, text: str) -> bool:
    t = normalize_digits(text.strip())
    if legacy._is(t, legacy.SKIP):
        line.update(status="unresolved", reason="customer_skipped")
        return True
    cands = [p for p in line["candidates"] if p in cat.items]
    pick = None
    if t.isdigit() and 1 <= int(t) <= len(cands):
        pick = cands[int(t) - 1]
    elif len(cands) == 1 and legacy._is(t, legacy.YES):
        pick = cands[0]
    elif cands and not t.isdigit():
        sub = Matcher([CatalogEntry(uuid.UUID(p), "", cat.items[p].name, []) for p in cands])
        res = sub.match_line(parse_line(t))
        if res.product_id is not None:
            pick = str(res.product_id)
    if pick is None:
        return False
    line["item_id"] = pick
    _route_matched(db, link, cat, line, qty_known=bool(line["qty"]))
    return True


# ---- questions, confirmation, order --------------------------------------------------------
def _price_label(line: dict) -> str:
    return str(Decimal(line["unit_price"]))


def _display_name(cat: Catalog, line: dict) -> str:
    item = cat.items[line["item_id"]]
    v = cat.variant(line["item_id"], line["variant_id"]) if line.get("variant_id") else None
    if v is not None and len(cat.variants.get(line["item_id"], [])) > 1:
        return f"{item.name} ({v.name})"
    return item.name


def _next_question(db: Session, link: TelegramLink, cat: Catalog, lines: list[dict]) -> str | None:
    for line in lines:
        if line["status"] == "need_variant":
            item = cat.items[line["item_id"]]
            opts = []
            for v in cat.variants[line["item_id"]]:
                price, _ = resolve_variant_price(db, link.business_id, link.customer_id, v)
                label = (f"{v.name} - {_money(price)}ج" if price is not None
                         else f"{v.name} - السعر عند الكاشير")
                if price is not None and is_generic_variant(v.name):
                    label = variant_label(v.name, price)
                opts.append(label)
            return (MSG_VARIANT.format(item=item.name) + "\n"
                    + "\n".join(f"{i}) {o}" for i, o in enumerate(opts, 1))
                    + "\nاكتب الرقم، أو 0 لو مش عايزه.")
        if line["status"] == "need_qty":
            item = cat.items[line["item_id"]]
            name = _display_name(cat, line)
            if item.sold_by == "weight":
                return MSG_QTY_WEIGHT.format(name=name, unit=item.unit_label or "كيلو")
            return f"كام «{name}» عايز؟ اكتب الكمية بالرقم."
        if line["status"] == "need_choice":
            opts = [cat.items[p].name for p in line["candidates"] if p in cat.items]
            if len(opts) != len(set(opts)):  # same name in two categories: disambiguate
                opts = [f"{cat.items[p].name} ({cat.items[p].category})"
                        for p in line["candidates"] if p in cat.items]
            if not opts:
                line["status"] = "unresolved"
                continue
            head = (f"قصدك «{opts[0]}» في «{line['raw']}»؟ اكتب 1 للتأكيد أو 0 لو لا."
                    if len(opts) == 1 else f"قصدك أنهي صنف في «{line['raw']}»؟")
            if len(opts) > 1:
                head += "\n" + "\n".join(f"{i}) {n}" for i, n in enumerate(opts, 1))
                head += "\nاكتب الرقم، أو 0 لو مش من دول."
            return head
    return None


def _advance(db, link, session, cat: Catalog, lines: list[dict]) -> str:
    q = _next_question(db, link, cat, lines)
    if q is not None:
        _save_menu(db, link, session, "clarifying", lines)
        return q
    if not any(ln["status"] == "ok" for ln in lines):
        legacy._clear(db, session)
        priced = [ln for ln in lines if ln["reason"] == "price_required"]
        if priced:
            return "\n".join(MSG_PRICE_REQUIRED.format(item=cat.items[ln["item_id"]].name)
                             for ln in priced)
        return legacy.MSG_NOTHING
    _save_menu(db, link, session, "confirming", lines)
    return _confirmation(db, link, cat, lines)


def _qty(q) -> str:
    d = Decimal(str(q))
    return str(int(d)) if d == d.to_integral_value() else str(q)


def _confirmation(db: Session, link: TelegramLink, cat: Catalog, lines: list[dict]) -> str:
    ok = [ln for ln in lines if ln["status"] == "ok"]
    rest = [ln for ln in lines if ln["status"] != "ok"]
    total = Decimal("0")
    out = ["ده طلبك:"]
    for ln in ok:
        price = Decimal(ln["unit_price"])
        qty = Decimal(str(ln["qty"]))
        row = f"• {_qty(ln['qty'])} × {_display_name(cat, ln)}"
        if settings.chat_show_prices:
            row += f" = {_money(price * qty)} (السعر {_money(price)})"
            total += price * qty
        out.append(row)
    if settings.chat_show_prices:
        out.append(f"الإجمالي: {_money(total)}")
    if rest:
        out.append("أصناف المحل هيراجعها معاك: " + "، ".join(f"«{ln['raw']}»" for ln in rest))
    out.append("اكتب «تمام» للتأكيد، أو «الغي»، أو ابعت صنف زيادة.")
    return "\n".join(out)


def _create_order(db: Session, link: TelegramLink, cat: Catalog, lines: list[dict],
                  status: str = "draft", notes: str | None = None) -> Order:
    customer = db.scalar(select(Customer).where(
        Customer.business_id == link.business_id, Customer.id == link.customer_id))
    order = orders_repo.create_order(db, link.business_id, {
        "customer_id": customer.id, "channel": "telegram",
        "source_text": "محادثة تيليجرام (وكيل المحادثة)", "notes": notes})
    total = Decimal("0")
    unresolved = 0
    # order_lines is unique on (order_id, variant_id): the same variant added twice
    # from buttons (cart lines are kept separate for display) becomes one line with
    # the summed quantity. A duplicate insert used to roll the whole confirm back.
    merged: list[dict] = []
    seen: dict[str, dict] = {}
    for ln in lines:
        key = ln.get("variant_id") if ln.get("status") == "ok" else None
        if key and key in seen:
            seen[key]["qty"] = float(Decimal(str(seen[key]["qty"]))
                                     + Decimal(str(ln["qty"])))
            continue
        ln = dict(ln)
        if key:
            seen[key] = ln
        merged.append(ln)
    lines = merged
    for ln in lines:
        if ln["status"] == "ok":
            v = cat.variant(ln["item_id"], ln["variant_id"])
            # re-resolve at commit time: the price the customer saw is the price we freeze
            price, source = resolve_variant_price(db, link.business_id, link.customer_id, v)
            if price is None:
                ln.update(status="unresolved", reason="price_required")
            else:
                qty = Decimal(str(ln["qty"]))
                item = cat.items[ln["item_id"]]
                db.add(OrderLine(
                    business_id=link.business_id, order_id=order.id, variant_id=v.id,
                    item_name=item.name, variant_name=v.name, quantity=qty,
                    unit_label=item.unit_label if item.sold_by == "weight" else None,
                    unit_price=price, price_source=source,
                    line_total=(price * qty).quantize(Decimal("0.01"))))
                total += price * qty
                continue
        if ln["status"] != "ok":
            unresolved += 1
            cands = ln.get("candidates") or ([ln["item_id"]] if ln.get("item_id") else [])
            db.add(OrderException(
                business_id=link.business_id, order_id=order.id, raw_text=ln["raw"][:500],
                reason="unclear_item", reason_detail=(ln.get("reason") or "not_found")[:30],
                customer_message=ln["raw"],
                quantity=(int(ln["qty"]) if isinstance(ln.get("qty"), int) else None),
                candidates=[{"menu_item_id": c} for c in cands]))
    order.total = total
    order.status = status
    db.flush()
    write_audit(db, business_id=link.business_id, entity="order", entity_id=str(order.id),
                action="create", payload={"source": "menu_chat_agent", "channel": "telegram",
                                          "lines": len(lines), "exceptions": unresolved,
                                          "status": status})
    return order


# ---- smalltalk (menu facts from the DB) ------------------------------------------------------
def _smalltalk(db: Session, link: TelegramLink, text: str, cat: Catalog) -> str | None:
    t = normalize(text)
    if t in legacy.GREET or t.rstrip("؟?!. ") in legacy.GREET:
        return random.choice(legacy.GREETING_VARIANTS)
    if t in legacy.THANKS:
        return legacy.MSG_THANKS
    if t in legacy.HELP_Q:
        return legacy.MSG_HELP
    if any(k in t for k in legacy.CATALOG_Q) and not any(
            ch.isdigit() for ch in normalize_digits(t)):
        cats = list_categories(db, link.business_id)
        if not cats:
            return legacy.MSG_DONT_GET
        out = ["الأقسام المتاحة:"]
        for c in cats:
            names = [i.name for i in category_items(cat, c.name)]
            out.append(f"• {c.name}: " + "، ".join(names[:8])
                       + (" وغيرهم" if len(names) > 8 else ""))
        out.append("اكتب اللي عايزه والكمية.")
        return "\n".join(out)
    if any(k in t for k in legacy.PRICE_Q) and not any(
            ch.isdigit() for ch in normalize_digits(t)):
        res = [r for r in cat.matcher.match_text(
            " ".join(w for w in t.split() if w not in {"كام", "سعر", "بكام", "اسعار"}))
            if r.product_id]
        if res:
            item = cat.items.get(str(res[0].product_id))
            if item is not None:
                parts = []
                for v in cat.variants.get(str(item.id), []):
                    price, _ = resolve_variant_price(
                        db, link.business_id, link.customer_id, v)
                    if price is None:
                        parts.append(f"{v.name}: السعر عند الكاشير")
                    elif is_generic_variant(v.name):
                        parts.append(variant_label(v.name, price))
                    else:
                        parts.append(f"{v.name}: {_money(price)}")
                return f"{item.name} - " + "، ".join(parts) + ". لو عايز تطلب اكتب الكمية."
        return "قولي اسم الصنف وأقولك سعره، مثال: «سعر الكفتة»."
    return None
