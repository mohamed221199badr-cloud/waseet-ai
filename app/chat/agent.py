"""Conversational order agent for Telegram (Milestone 5).

Division of labour (hard rule): rules and the database decide, the customer confirms,
the LLM (optional) only suggests.
- Products, quantities and ambiguity come from the matching engine, never from the LLM.
- Prices and stock warnings come from the database only (resolve_price, stock_quantity).
- Anything the engine cannot settle and the customer does not settle becomes an
  OrderException for the owner, exactly like the one-shot intake.
- Nothing is an order until the customer says yes; the order is still a DRAFT that the
  owner confirms. Stock is never changed here.
"""

import copy
import re
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from rapidfuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import settings
from app.llm import client as llm
from app.matching import CatalogEntry, Matcher
from app.matching.normalize import normalize, normalize_digits
from app.matching.parser import parse_line
from app.models import Product
from app.models_orders import ChatSession, Customer, OrderException, TelegramLink
from app.repositories import intake as intake_repo
from app.repositories import orders as orders_repo

MAX_TEXT = 5000
MAX_LINES = 60

YES = {normalize(w) for w in (
    "تمام", "ايوه", "ايوا", "أيوه", "اه", "آه", "ماشي", "اكد", "أكد", "اكده", "تمام كده",
    "موافق", "اوكي", "اوك", "ok", "okay", "yes", "نعم", "تأكيد", "اتفقنا", "يلا")}
NO = {normalize(w) for w in (
    "لا", "لأ", "لاا", "الغي", "ألغي", "الغاء", "إلغاء", "كنسل", "cancel", "no", "مش عايز")}
CANCEL = {normalize(w) for w in ("الغي", "ألغي", "الغاء", "إلغاء", "كنسل", "cancel", "/cancel")}
SKIP = {normalize(w) for w in ("0", "لا", "لأ", "مش دول", "تخطى", "سيبه", "no")}

MSG_CANCELLED = "تمام، ألغيت الطلب. ابعت طلب جديد في أي وقت."
MSG_NOTHING = ("ما لقيتش أصناف من اللي كتبتها في الكتالوج. جرّب تكتب الاسم بشكل تاني، "
               "أو كلّم المحل مباشرة.")
MSG_AFTER_CONFIRM = "استلمنا طلبك (رقم {oid}). لسه مسودة لحد ما المحل يعتمده."
MSG_REASK_CHOICE = "ما فهمتش. اكتب رقم من القائمة، أو 0 لو مش من دول."
MSG_REASK_QTY = "ما فهمتش الكمية. اكتب رقم بس، مثال: 3"
MSG_REASK_CONFIRM = "اكتب «تمام» لتأكيد الطلب، أو «الغي» لإلغائه، أو ابعت صنف زيادة."


# ---- session storage ------------------------------------------------------------------
def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _load(db: Session, link: TelegramLink) -> ChatSession | None:
    s = db.scalar(select(ChatSession).where(
        ChatSession.chat_id == link.chat_id, ChatSession.business_id == link.business_id,
        ChatSession.customer_id == link.customer_id))
    ttl = timedelta(minutes=settings.chat_session_ttl_minutes)
    if s is not None and s.updated_at < _now() - ttl:
        db.delete(s)
        db.flush()
        return None
    return s


def _save(db: Session, link: TelegramLink, session: ChatSession | None, state: str,
          lines: list[dict]) -> None:
    if session is None:
        session = ChatSession(business_id=link.business_id, customer_id=link.customer_id,
                              chat_id=link.chat_id)
        db.add(session)
    session.state = state
    session.draft = {"lines": lines}
    session.updated_at = _now()
    db.flush()


def _clear(db: Session, session: ChatSession | None) -> None:
    if session is not None:
        db.delete(session)
        db.flush()


# ---- text -> lines --------------------------------------------------------------------
def _name_map(db: Session, business_id) -> dict[str, Product]:
    return {str(p.id): p for p in db.scalars(select(Product).where(
        Product.business_id == business_id, Product.is_active.is_(True)))}


def _lines_from_text(db: Session, business_id, text: str, products: dict[str, Product]):
    """Run the matching engine. Returns line dicts; the engine alone decides statuses."""
    out: list[dict] = []
    seen: set[str] = set()
    results = []
    for r in intake_repo.build_matcher(db, business_id).match_text(text):
        if r.status != "matched" and r.reason in ("not_found", "low_confidence", "ambiguous"):
            split = _split_conjunctions(db, business_id, r.raw)
            if split:
                results.extend(split)
                continue
        results.append(r)
    for r in results:
        line = {"raw": r.raw[:200], "qty": r.quantity, "product_id": None, "candidates": [],
                "status": "unresolved", "reason": r.reason or "not_found", "asked": 0}
        pid = str(r.product_id) if r.product_id else None
        if r.status == "matched" and pid not in seen:
            seen.add(pid)
            line.update(product_id=pid, status="ok", reason=None)
        elif r.status == "matched":
            line["reason"] = "duplicate_product"
        elif r.reason in ("missing_quantity", "unclear_quantity") and pid:
            line.update(product_id=pid, status="need_qty")
        elif r.reason in ("ambiguous", "low_confidence") and r.candidates:
            line.update(candidates=[str(c.product_id) for c in r.candidates],
                        status="need_choice")
        out.append(line)
    # optional LLM hint for lines the rules could not place at all (never auto-accepted)
    if llm.llm_enabled():
        for line in out:
            if line["status"] == "unresolved" and line["reason"] == "not_found":
                names = _top_names(line["raw"], products)
                idx = llm.suggest_product(line["raw"], [n for _, n in names])
                if idx is not None:
                    line.update(candidates=[names[idx][0]], status="need_choice",
                                reason="llm_suggestion")
    return out


_CONJ = re.compile(r"\s+و\s*(?=\S)|(?<=\S)\s+و(?=\S)")


def _split_conjunctions(db, business_id, raw: str) -> list:
    """'٣ شكاير رز وكرتونتين زيت' on one line: split on the Arabic 'و' (also glued to the
    next word) and keep the split only if EVERY part settles on a product, so a normal name
    that contains 'و' is never cut by accident. Returns the engine results or []."""
    parts = [p.strip() for p in _CONJ.split(raw) if p and p.strip()]
    if len(parts) < 2:
        return []
    matcher = intake_repo.build_matcher(db, business_id)
    results = [r for p in parts for r in matcher.match_text(p)]
    return results if len(results) == len(parts) and all(
        r.product_id is not None for r in results) else []


def _top_names(raw: str, products: dict[str, Product], n: int = 8) -> list[tuple[str, str]]:
    key = normalize(raw)
    ranked = sorted(
        products.items(), key=lambda kv: -fuzz.token_set_ratio(key, normalize(kv[1].name)))
    return [(pid, p.name) for pid, p in ranked[:n]]


# ---- questions ------------------------------------------------------------------------
def _next_question(lines: list[dict], products: dict[str, Product]) -> str | None:
    for line in lines:
        if line["status"] == "need_qty":
            return f"كام «{products[line['product_id']].name}» عايز؟ اكتب الكمية بالرقم."
        if line["status"] == "need_choice":
            opts = [products[p].name for p in line["candidates"] if p in products]
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


def _confirmation(db: Session, link: TelegramLink, lines: list[dict],
                  products: dict[str, Product]) -> str:
    ok = [ln for ln in lines if ln["status"] == "ok"]
    rest = [ln for ln in lines if ln["status"] != "ok"]
    customer_id = link.customer_id
    total = Decimal("0")
    out = ["ده طلبك:"]
    for ln in ok:
        p = products[ln["product_id"]]
        price, _ = orders_repo.resolve_price(db, link.business_id, customer_id, p)
        row = f"• {ln['qty']} × {p.name}"
        if settings.chat_show_prices:
            row += f" = {price * ln['qty']} (السعر {price})"
            total += price * ln["qty"]
        if p.stock_quantity < ln["qty"]:
            row += "  ⚠ الكمية ممكن مش متوفرة، المحل هيراجعها"
        out.append(row)
    if settings.chat_show_prices:
        out.append(f"الإجمالي: {total}")
    if rest:
        out.append("أصناف المحل هيراجعها معاك: " + "، ".join(f"«{ln['raw']}»" for ln in rest))
    out.append("اكتب «تمام» للتأكيد، أو «الغي»، أو ابعت صنف زيادة.")
    return "\n".join(out)


# ---- replies --------------------------------------------------------------------------
def _is(text: str, words: set[str]) -> bool:
    return normalize(text) in words


def _apply_choice(line: dict, text: str, products: dict[str, Product]) -> bool:
    t = normalize_digits(text.strip())
    if _is(t, SKIP):
        line.update(status="unresolved", reason="customer_skipped")
        return True
    cands = [p for p in line["candidates"] if p in products]
    pick = None
    if t.isdigit() and 1 <= int(t) <= len(cands):
        pick = cands[int(t) - 1]
    elif len(cands) == 1 and _is(t, YES):
        pick = cands[0]
    elif cands and not t.isdigit():
        # the customer typed a name: accept only a single clear winner among the options
        sub = Matcher([CatalogEntry(uuid.UUID(p), products[p].sku, products[p].name, [])
                       for p in cands])
        res = sub.match_line(parse_line(t))
        if res.product_id is not None or (res.candidates and res.reason in ("missing_quantity",)):
            pick = str(res.product_id)
    if pick is None:
        return False
    line["product_id"] = pick
    if line["qty"] and line["qty"] > 0:
        line["status"] = "ok"
    else:
        line["status"] = "need_qty"
    return True


def _apply_qty(line: dict, text: str) -> bool:
    t = normalize_digits(text.strip())
    # parse "صنف <reply>" so digits, Arabic digits and number words reuse the engine's parser
    parsed = parse_line("صنف " + t)
    qty = parsed.quantity if parsed.key == "صنف" else None
    if qty is None or qty <= 0 or qty > 100000:
        return False
    line.update(qty=qty, status="ok")
    return True


def _create_order(db: Session, link: TelegramLink, lines: list[dict]) -> str:
    customer = db.scalar(select(Customer).where(
        Customer.business_id == link.business_id, Customer.id == link.customer_id))
    order = orders_repo.create_order(db, link.business_id, {
        "customer_id": customer.id, "channel": "telegram",
        "source_text": "محادثة تيليجرام (وكيل المحادثة)", "notes": None})
    unresolved = 0
    for ln in lines:
        failed = ln["status"] != "ok"
        if not failed:
            try:
                orders_repo.add_item(db, order, uuid.UUID(ln["product_id"]), ln["qty"])
            except (orders_repo.ProductUnavailable, orders_repo.DuplicateItem):
                failed = True
        if failed:
            unresolved += 1
            cands = ln.get("candidates") or ([ln["product_id"]] if ln.get("product_id") else [])
            db.add(OrderException(
                business_id=link.business_id, order_id=order.id, raw_text=ln["raw"][:500],
                reason=(ln.get("reason") or "not_found")[:30], quantity=ln.get("qty"),
                candidates=[{"product_id": c} for c in cands]))
    db.flush()
    write_audit(db, business_id=link.business_id, entity="order", entity_id=str(order.id),
                action="create", payload={"source": "chat_agent", "channel": "telegram",
                                          "lines": len(lines), "exceptions": unresolved})
    return MSG_AFTER_CONFIRM.format(oid=str(order.id)[:8])


def _advance(db, link, session, lines, products) -> str:
    """After any change: ask the next question, or show the confirmation."""
    q = _next_question(lines, products)
    if q is not None:
        _save(db, link, session, "clarifying", lines)
        return q
    if not any(ln["status"] == "ok" for ln in lines):
        _clear(db, session)
        return MSG_NOTHING
    _save(db, link, session, "confirming", lines)
    return _confirmation(db, link, lines, products)


def handle_message(db: Session, link: TelegramLink, text: str) -> str:
    """One customer message -> one reply. The caller commits."""
    if len(text) > MAX_TEXT:
        return "الرسالة طويلة جدًا. قسّمها لرسائل أقصر."
    customer = db.scalar(select(Customer).where(
        Customer.business_id == link.business_id, Customer.id == link.customer_id))
    if customer is None or not customer.is_active:
        return "الحساب ده متوقف عند المحل. كلّم المحل مباشرة."
    session = _load(db, link)
    products = _name_map(db, link.business_id)
    lines: list[dict] = copy.deepcopy(session.draft.get("lines", [])) if session else []
    state = session.state if session else "collecting"

    if session and _is(text, CANCEL):
        _clear(db, session)
        return MSG_CANCELLED

    if state == "clarifying":
        line = next((ln for ln in lines if ln["status"] in ("need_qty", "need_choice")), None)
        if line is not None:
            ok = (_apply_qty(line, text) if line["status"] == "need_qty"
                  else _apply_choice(line, text, products))
            if not ok:
                line["asked"] = line.get("asked", 0) + 1
                if line["asked"] >= 3:  # stop nagging: the owner will settle this line
                    line.update(status="unresolved", reason="customer_unclear")
                else:
                    _save(db, link, session, "clarifying", lines)
                    return MSG_REASK_QTY if line["status"] == "need_qty" else (
                        MSG_REASK_CHOICE + "\n" + (_next_question(lines, products) or ""))
            return _advance(db, link, session, lines, products)

    if state == "confirming":
        label = "yes" if _is(text, YES) else "no" if _is(text, NO) else None
        if label == "yes":
            reply = _create_order(db, link, lines)
            _clear(db, session)
            return reply
        if label == "no":
            _clear(db, session)
            return MSG_CANCELLED
        # otherwise: more items for the same order
        new = _lines_from_text(db, link.business_id, text, products)
        if not new:
            return MSG_REASK_CONFIRM
        have = {ln["product_id"] for ln in lines if ln.get("product_id")}
        for ln in new:
            if ln["status"] == "ok" and ln["product_id"] in have:
                ln.update(status="unresolved", reason="duplicate_product")
        lines += new
        return _advance(db, link, session, lines, products)

    # collecting (new message, or an idle session)
    new = _lines_from_text(db, link.business_id, text, products)
    if not new:
        return "ما لقيتش بنود في الرسالة. اكتب الصنف والكمية، مثال: ٣ سكر"
    if len(new) > MAX_LINES:
        return "عدد البنود كبير. قسّم الطلب لأكتر من رسالة."
    return _advance(db, link, session, new, products)
