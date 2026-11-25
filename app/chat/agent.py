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
import random
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
MIN_SUGGEST_SIMILARITY = 55
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
               "أو اكتب «الأصناف» أعرضلك المتاح.")
MSG_AFTER_CONFIRM = "استلمنا طلبك (رقم {oid}). لسه مسودة لحد ما المحل يعتمده."
MSG_REASK_CHOICE = "ما فهمتش. اكتب رقم من القائمة، أو 0 لو مش من دول."
MSG_REASK_QTY = "ما فهمتش الكمية. اكتب رقم بس، مثال: 3"
MSG_REASK_CONFIRM = "اكتب «تمام» لتأكيد الطلب، أو «الغي» لإلغائه، أو ابعت صنف زيادة."


GREET = {normalize(w) for w in (
    "السلام عليكم", "سلام عليكم", "سلام", "مرحبا", "اهلا", "أهلا", "هاي", "هلا", "hi", "hello",
    "ازيك", "إزيك", "صباح الخير", "مساء الخير", "صباح النور", "ازيك يا ريس", "ازاي حضرتك")}
THANKS = {normalize(w) for w in ("شكرا", "شكراً", "تسلم", "متشكر", "الله يسلمك", "ميرسي", "thanks")}
HELP_Q = {normalize(w) for w in ("مساعدة", "help", "ازاي", "ازاي اطلب", "اعمل ايه")}
CATALOG_Q = ("الاصناف", "المنتجات", "عندكم ايه", "عندكو ايه", "عندك ايه", "فيه ايه", "المتاح")
PRICE_Q = ("سعر", "بكام", "كام سعر", "اسعار")

MSG_GREETING = ("أهلاً بيك! أنا بوت استلام الطلبات. اكتب اللي عايزه والكمية، مثال: ٣ سكر "
                "وكرتونتين زيت. أو اكتب «الأصناف» أعرضلك المتاح.")
GREETING_VARIANTS = (
    MSG_GREETING,
    "أهلاً وسهلاً! ابعتلي الصنف والكمية وأنا أجهزلك الطلب. «الأصناف» تعرضلك المتاح.",
    "يا مرحب! عايز تطلب إيه؟ اكتب الصنف والكمية، مثال: ٣ سكر.",
)
MSG_THANKS = "العفو! لو عايز حاجة تانية ابعت طلبك في أي وقت."
MSG_HELP = ("بتكتب الصنف والكمية، وأنا أسألك لو في حاجة مش واضحة وأعرضلك الطلب تأكده.\n"
            "مثال: ٣ سكر وكرتونتين زيت. «الأصناف» لعرض المتاح، «الغي» لإلغاء الطلب.")
MSG_DONT_GET = ("مش فاهم قصدك. اكتب الصنف والكمية، مثال: ٣ سكر. أو اكتب «الأصناف» "
                "أعرضلك المتاح، أو كلّم المحل مباشرة.")


def _smalltalk(db: Session, link: TelegramLink, text: str, products: dict,
               facts_only: bool = False) -> str | None:
    """Natural replies for non-order messages. All facts (names, prices) come from the DB;
    this is plain rules, no model. Returns None when the text looks like an order."""
    t = normalize(text)
    if not facts_only and (t in GREET or t.rstrip("؟?!. ") in GREET):
        return random.choice(GREETING_VARIANTS)
    if not facts_only and t in THANKS:
        return MSG_THANKS
    if t in HELP_Q:
        return MSG_HELP
    if any(k in t for k in CATALOG_Q) and not any(ch.isdigit() for ch in normalize_digits(t)):
        names = sorted(p.name for p in products.values())
        return ("المتاح عندنا:\n" + "\n".join(f"• {n}" for n in names[:30])
                + "\nاكتب اللي عايزه والكمية.") if names else MSG_DONT_GET
    if any(k in t for k in PRICE_Q) and not any(ch.isdigit() for ch in normalize_digits(t)):
        res = [r for r in intake_repo.build_matcher(db, link.business_id).match_text(
            " ".join(w for w in t.split() if w not in {"كام", "سعر", "بكام", "اسعار"}))
            if r.product_id]
        if res:
            p = products.get(str(res[0].product_id))
            if p is not None:
                price, _ = orders_repo.resolve_price(db, link.business_id, link.customer_id, p)
                return f"{p.name}: {price}. لو عايز تطلب اكتب الكمية."
        return "قولي اسم الصنف وأقولك سعره، مثال: «سعر السكر»."
    return None


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
            # only for lines that look like an order (have a quantity), never for chatter
            if (line["status"] == "unresolved" and line["reason"] == "not_found"
                    and line["qty"]):
                names = _top_names(line["raw"], products)
                idx = llm.suggest_product(line["raw"], [n for _, n in names])
                # the suggestion must also look like the typed word, or it is dropped
                if idx is not None and fuzz.partial_ratio(
                        normalize(line["raw"]), normalize(names[idx][1])) >= MIN_SUGGEST_SIMILARITY:
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


def _llm_turn(db, link, text, products, pending=None):
    """LLM_MODE=chat: let the model read the message. Items it proposes are re-written as
    "<qty> <name>" and sent through the matching engine, so product, quantity and ambiguity
    are still decided by rules + DB. Returns (reply_prefix, lines) or None to fall back."""
    out = llm.converse(sorted(p.name for p in products.values()), text, pending)
    if out is None:
        return None
    said = _numbers_in(text)
    lines = []
    for name, qty in out["items"]:
        # a quantity the customer never wrote is not trusted: ask "كام؟" instead
        q = f"{qty} " if qty in said or qty == 1 else ""
        lines += _lines_from_text(db, link.business_id, f"{q}{name}", products)
    return out["reply"], _real_lines(lines)


def _numbers_in(text: str) -> set[int]:
    """Quantities actually present in the customer's message: digits, number words, duals."""
    from app.matching.parser import _NUMBER_WORDS
    t = normalize_digits(normalize(text))
    nums = {int(x) for x in re.findall(r"\d+", t)}
    nums |= {_NUMBER_WORDS[w] for w in t.split() if w in _NUMBER_WORDS}
    return nums


def _real_lines(lines: list[dict]) -> list[dict]:
    """Drop chatter: an unmatched line with no quantity is not an item the customer ordered."""
    return [x for x in lines if not (x["status"] == "unresolved" and not x["qty"])]


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
                # B3: enum reason + the matcher/agent code kept in reason_detail
                business_id=link.business_id, order_id=order.id, raw_text=ln["raw"][:500],
                reason="unclear_item", reason_detail=(ln.get("reason") or "not_found")[:30],
                customer_message=ln["raw"], quantity=ln.get("qty"),
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


_HISTORY: dict = {}      # LLM_MODE=open only: link id -> [(ts, role, text)], in memory, trial use


def _history(link) -> list[tuple[str, str]]:
    cutoff = _now() - timedelta(minutes=settings.chat_session_ttl_minutes)
    h = [x for x in _HISTORY.get(str(link.id), []) if x[0] > cutoff]
    _HISTORY[str(link.id)] = h
    return [(r, t) for _, r, t in h]


def handle_message(db: Session, link: TelegramLink, text: str) -> str:
    """One customer message -> one reply. The caller commits."""
    prior = _history(link) if llm.open_mode() else []
    reply = _handle(db, link, text, prior)
    if llm.open_mode():
        key = str(link.id)
        turns = [(_now(), "user", text[:500]), (_now(), "assistant", reply[:500])]
        _HISTORY[key] = (_HISTORY.get(key, []) + turns)[-12:]
    return reply


def _handle(db: Session, link: TelegramLink, text: str, prior: list) -> str:
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
                chat = None if llm.chat_mode() else _smalltalk(db, link, text, products)
                if chat is None and llm.chat_mode():
                    turn = _llm_turn(db, link, text, products, _next_question(lines, products))
                    if turn is not None and not turn[1]:
                        chat = turn[0] or MSG_DONT_GET
                    elif turn is not None:
                        lines += turn[1]
                        return (turn[0] + "\n\n" if turn[0] else "") + _advance(
                            db, link, session, lines, products)
                if chat is None and llm.chat_mode():
                    chat = _smalltalk(db, link, text, products)   # model failed: rule text
                if chat is not None:      # greeting/question: answer, then repeat the question
                    _save(db, link, session, "clarifying", lines)
                    return chat + "\n\n" + (_next_question(lines, products) or "")
                extra = _real_lines(_lines_from_text(db, link.business_id, text, products))
                if any(x["status"] != "unresolved" for x in extra):
                    # the customer moved on to new items: add them, keep the open question
                    have = {ln["product_id"] for ln in lines if ln.get("product_id")}
                    for x in extra:
                        if x["status"] == "ok" and x["product_id"] in have:
                            x.update(status="unresolved", reason="duplicate_product")
                    lines += extra
                    return _advance(db, link, session, lines, products)
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
        chat = None if llm.chat_mode() else _smalltalk(db, link, text, products)
        if chat is None and llm.chat_mode() and not _real_lines(
                _lines_from_text(db, link.business_id, text, products)):
            turn = _llm_turn(db, link, text, products, "تأكيد الطلب")
            if turn is not None and turn[1]:
                lines += turn[1]
                return (turn[0] + "\n\n" if turn[0] else "") + _advance(
                    db, link, session, lines, products)
            if turn is not None:
                chat = turn[0] or MSG_REASK_CONFIRM
        if chat is None and llm.chat_mode():
            chat = _smalltalk(db, link, text, products)
        if chat is not None:
            return chat + "\n\n" + _confirmation(db, link, lines, products)
        # otherwise: more items for the same order
        new = _real_lines(_lines_from_text(db, link.business_id, text, products))
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
    parsed = bool(new) and not all(x["status"] == "unresolved" and not x["qty"] for x in new)
    prefix = ""
    settled = any(x["status"] != "unresolved" for x in new)
    if llm.chat_mode() and not settled:
        facts = _smalltalk(db, link, text, products, facts_only=True)  # catalog/price: DB only
        if facts is not None:
            _clear(db, session)
            return facts
        if llm.open_mode():
            free = llm.open_reply(prior, text)      # shown as is; no order power
            _clear(db, session)
            if free:
                return free
            return _smalltalk(db, link, text, products) or MSG_DONT_GET
        turn = _llm_turn(db, link, text, products)
        if turn is not None:
            prefix, new = turn
            if not new:
                _clear(db, session)
                return prefix or _smalltalk(db, link, text, products) or MSG_DONT_GET
            parsed = True
    if not parsed:
        chat = _smalltalk(db, link, text, products)
        _clear(db, session)
        return chat if chat is not None else MSG_DONT_GET
    if len(new) > MAX_LINES:
        return "عدد البنود كبير. قسّم الطلب لأكتر من رسالة."
    return (prefix + "\n\n" if prefix else "") + _advance(db, link, session, new, products)
