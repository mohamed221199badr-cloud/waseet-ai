"""UI v1.8: fully button-driven menu ordering (menu mode only).

Each screen is a function over (db, link) returning (text, button_rows). Button taps
arrive as codec Callbacks; text arrives only while a custom quantity is awaited.
Everything writes the SAME draft lines the text flow uses (draft kind "menu"), so
confirmation and order creation stay in menu_agent - no duplicated business logic,
no LLM anywhere in this flow.

Session ui state (inside ChatSession.draft["ui"]):
    screen: cats | items | item | qty | cart | clear_confirm
    category, page, item_id, variant_id: the current selection
    await_qty: True while a custom number is expected (next text message)
    msg_id: the bot's last menu message, for edit-in-place navigation
"""

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.chat import agent as legacy
from app.chat import callbacks, menu_agent
from app.chat.menu_catalog import (
    category_items,
    list_categories,
    load_catalog,
    resolve_variant_price,
)
from app.models_orders import ChatSession, TelegramLink

PAGE_SIZE = 6

MSG_STALE = "الاختيار ده مش متاح دلوقتي. اختار من الأصناف:"
MSG_CATS = "اختار من الأصناف:"
MSG_CLEARED = "اتفضت السلة. نبدأ من الأول:"
MSG_CART_EMPTY = "السلة فاضية لسه. اختار من الأصناف:"
MSG_PRICE_REQUIRED = (
    "«{item}» سعره بيتحدد مع الكاشير، فمش هقدر أضيفه من هنا. "
    "قوله في المحل أو اختار صنف تاني."
)


def _session(db: Session, link: TelegramLink) -> ChatSession | None:
    return db.scalar(
        select(ChatSession).where(ChatSession.chat_id == link.chat_id)
    )


def _ui(session: ChatSession | None) -> dict | None:
    if session is None or not isinstance(session.draft, dict):
        return None
    if session.draft.get("kind") != "menu":
        return None
    return session.draft.get("ui")


def in_flow(db: Session, link: TelegramLink) -> bool:
    return _ui(_session(db, link)) is not None


def wants_text(db: Session, link: TelegramLink) -> bool:
    """True when the next text message is a custom quantity for this flow."""
    ui = _ui(_session(db, link))
    return bool(ui and ui.get("await_qty"))


def _save(db: Session, session: ChatSession, ui: dict) -> None:
    """Persist the menu draft (lines untouched here) with updated ui sub-state."""
    draft = dict(session.draft or {})
    draft["ui"] = ui
    session.draft = draft
    flag_modified(session, "draft")
    session.updated_at = legacy._now()
    db.add(session)
    db.flush()


def _ensure_session(db: Session, link: TelegramLink) -> tuple[ChatSession, dict, list]:
    session = _session(db, link)
    if session is None:
        # first contact is a button tap: open the same draft shape the text flow uses
        session = ChatSession(business_id=link.business_id, customer_id=link.customer_id,
                              chat_id=link.chat_id, state="collecting",
                              draft={"kind": "menu", "lines": []})
        db.add(session)
        db.flush()
    draft = dict(session.draft or {"kind": "menu", "lines": []})
    draft.setdefault("kind", "menu")
    lines = draft.setdefault("lines", [])
    ui = draft.setdefault("ui", {"screen": "cats"})
    session.draft = draft
    flag_modified(session, "draft")
    db.add(session)
    return session, ui, lines


def _ok_lines(lines: list[dict]) -> list[dict]:
    return [ln for ln in lines if ln.get("status") == "ok" and ln.get("qty")]


# ---------- screens ----------

def _cats_rows(db: Session, link: TelegramLink, lines: list[dict]) -> list[list[tuple[str, str]]]:
    rows = [[(c.name, callbacks.build("cat", c.name, "p0"))]
            for c in list_categories(db, link.business_id)]
    if _ok_lines(lines):
        rows.append([("🛒 السلة", callbacks.build("cart"))])
    return rows


def _items_rows(cat, category: str, page: int) -> tuple[list[list[tuple[str, str]]], int, int]:
    items = category_items(cat, category)
    total_pages = max(1, (len(items) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]
    rows: list[list[tuple[str, str]]] = [
        [(f"{i.name}", callbacks.build("it", i.id))] for i in chunk
    ]
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(("◀ السابق", callbacks.build("cat", category, f"p{page - 1}")))
        nav.append((f"{page + 1}/{total_pages}", callbacks.build("noop")))
        if page < total_pages - 1:
            nav.append(("التالي ▶", callbacks.build("cat", category, f"p{page + 1}")))
        rows.append(nav)
    rows.append([("🔙 الأصناف", callbacks.build("cats"))])
    return rows, page, total_pages


def _variants_rows(cat, item_id: str, category: str, page: int) -> list[list[tuple[str, str]]]:
    rows = []
    for v in cat.variants.get(item_id, []):
        price = "بسعر الكاشير" if v.price is None else f"{menu_agent._money(v.price)}ج"
        rows.append([(f"{v.name} - {price}",
                      callbacks.build("var", v.id))])
    rows.append([("🔙 رجوع", callbacks.build("cat", category, f"p{page}"))])
    return rows


def _qty_rows(item, item_id: str, variant_id: str) -> list[list[tuple[str, str]]]:
    if item.sold_by == "weight":
        quick = [("نص كيلو", 500), ("كيلو", 1000), ("كيلو ونص", 1500), ("٢ كيلو", 2000)]
        row1 = [(label, callbacks.build("wq", g)) for label, g in quick[:2]]
        row2 = [(label, callbacks.build("wq", g)) for label, g in quick[2:]]
    else:
        row1 = [(str(n), callbacks.build("uq", n)) for n in (1, 2, 3)]
        row2 = [(str(n), callbacks.build("uq", n)) for n in (5, 10)]
    custom = [("✍️ كمية تانية", callbacks.build("qcustom"))]
    back = [("🔙 رجوع", callbacks.build("it", item_id))]
    return [row1, row2, custom, back]


def _cart_text(db: Session, link: TelegramLink, cat, lines: list[dict]) -> str:
    ok = _ok_lines(lines)
    if not ok:
        return MSG_CART_EMPTY
    parts, total, unknown = [], Decimal("0"), False
    for ln in ok:
        item = cat.items.get(ln["item_id"])
        variant = next((v for v in cat.variants.get(ln["item_id"], [])
                        if str(v.id) == ln["variant_id"]), None)
        if item is None or variant is None:
            continue
        qty = Decimal(str(ln["qty"]))
        qty_txt = f"{qty.normalize()} كيلو" if item.sold_by == "weight" else f"{qty.normalize()}"
        price, _src = resolve_variant_price(db, link.business_id, link.customer_id,
                                            variant)
        if price is None:
            unknown = True
            parts.append(f"- {item.name} ({variant.name}) × {qty_txt}")
        else:
            total += price * qty
            parts.append(
                f"- {item.name} ({variant.name}) × {qty_txt} = {menu_agent._money(price * qty)}ج")
    tail = f"الإجمالي: {menu_agent._money(total)}ج"
    if unknown:
        tail += " (في أصناف سعرها عند الكاشير)"
    return "السلة:\n" + "\n".join(parts) + f"\n{tail}"


def _cart_rows() -> list[list[tuple[str, str]]]:
    return [
        [("✅ تأكيد الطلب", callbacks.build("cok"))],
        [("➕ أضف صنف", callbacks.build("cadd"))],
        [("🧹 إفراغ السلة", callbacks.build("cclr"))],
        [("❌ إلغاء الطلب", callbacks.build("ccancel"))],
    ]


def entry(db: Session, link: TelegramLink) -> tuple[str, list[list[tuple[str, str]]]]:
    """Category keyboard entry point (greeting / «الأصناف» in menu mode)."""
    session, ui, lines = _ensure_session(db, link)
    ui.update(screen="cats", await_qty=False)
    _save(db, session, ui)
    return MSG_CATS, _cats_rows(db, link, lines)


# ---------- callback handling ----------

def handle_callback(db: Session, link: TelegramLink, cb: callbacks.Callback,
                    msg_id: int | None) -> tuple[str, list[list[tuple[str, str]]]]:
    """One button tap -> one screen. msg_id is the tapped message (edit target)."""
    cat = load_catalog(db, link.business_id)
    session, ui, lines = _ensure_session(db, link)
    ui["await_qty"] = False
    if msg_id is not None:
        ui["msg_id"] = msg_id

    def stale() -> tuple[str, list[list[tuple[str, str]]]]:
        ui.update(screen="cats")
        _save(db, session, ui)
        return MSG_STALE, _cats_rows(db, link, lines)

    if cb.action == "noop":
        _save(db, session, ui)
        return "", []

    if cb.action in ("cats", "cadd"):
        ui.update(screen="cats")
        _save(db, session, ui)
        return MSG_CATS, _cats_rows(db, link, lines)

    if cb.action == "cat":
        category, page = cb.args[0], int(cb.args[1][1:])
        if not category_items(cat, category):
            return stale()
        rows, page, _ = _items_rows(cat, category, page)
        ui.update(screen="items", category=category, page=page)
        _save(db, session, ui)
        return f"أصناف «{category}»:", rows

    if cb.action == "it":
        item_id = cb.args[0]
        item = cat.items.get(item_id)
        if item is None:
            return stale()
        variants = cat.variants.get(item_id, [])
        if not variants:
            return stale()
        category = ui.get("category", "")
        page = int(ui.get("page", 0))
        if len(variants) > 1:
            ui.update(screen="item", item_id=item_id)
            _save(db, session, ui)
            return (menu_agent.MSG_VARIANT.format(item=item.name),
                    _variants_rows(cat, item_id, category, page))
        variant = variants[0]
        price, _src = resolve_variant_price(db, link.business_id, link.customer_id,
                                            variant)
        if price is None:
            return MSG_PRICE_REQUIRED.format(item=item.name), \
                _variants_rows(cat, item_id, category, page) if len(variants) > 1 else \
                _items_rows(cat, category, page)[0]
        ui.update(screen="qty", item_id=item_id, variant_id=str(variant.id))
        _save(db, session, ui)
        unit = "كيلو" if item.sold_by == "weight" else ""
        return (f"كام «{item.name}» عايز؟ {unit}".strip() + ":",
                _qty_rows(item, item_id, str(variant.id)))

    if cb.action == "var":
        # 64-byte budget: only the variant id travels; the item resolves through it
        variant_id = cb.args[0]
        item_id = next((iid for iid, vs in cat.variants.items()
                        if any(str(v.id) == variant_id for v in vs)), None)
        item = cat.items.get(item_id or "")
        variant = next((v for v in cat.variants.get(item_id or "", [])
                        if str(v.id) == variant_id), None)
        if item is None or variant is None or item_id is None:
            return stale()
        price, _src = resolve_variant_price(db, link.business_id, link.customer_id,
                                            variant)
        if price is None:
            return (MSG_PRICE_REQUIRED.format(item=item.name),
                    _variants_rows(cat, item_id, ui.get("category", ""),
                                   int(ui.get("page", 0))))
        ui.update(screen="qty", item_id=item_id, variant_id=variant_id)
        _save(db, session, ui)
        return f"كام «{item.name}» عايز؟:", _qty_rows(item, item_id, variant_id)

    if cb.action in ("wq", "uq"):
        item_id, variant_id = ui.get("item_id"), ui.get("variant_id")
        item = cat.items.get(item_id or "")
        variant = next((v for v in cat.variants.get(item_id or "", [])
                        if str(v.id) == (variant_id or "")), None)
        if item is None or variant is None:
            return stale()
        qty = Decimal(cb.args[0]) / 1000 if cb.action == "wq" else Decimal(cb.args[0])
        lines.append({"raw": item.name[:200], "qty": float(qty), "item_id": item_id,
                      "variant_id": variant_id, "candidates": [], "status": "ok",
                      "reason": "button", "asked": 0})
        ui.update(screen="cart")
        _save(db, session, ui)
        return _cart_text(db, link, cat, lines), _cart_rows()

    if cb.action == "qcustom":
        item = cat.items.get(ui.get("item_id") or "")
        if item is None:
            return stale()
        ui["await_qty"] = True
        _save(db, session, ui)
        unit = "بالكيلو (مثال: 2.5)" if item.sold_by == "weight" else "(مثال: 3)"
        return f"اكتب الكمية {unit}:", []

    if cb.action == "cart":
        ui.update(screen="cart")
        _save(db, session, ui)
        if not _ok_lines(lines):
            return MSG_CART_EMPTY, _cats_rows(db, link, lines)
        return _cart_text(db, link, cat, lines), _cart_rows()

    if cb.action == "cclr":
        ui.update(screen="clear_confirm")
        _save(db, session, ui)
        return ("متأكد إنك عايز تفضي السلة؟",
                [[("نعم، افضيها", callbacks.build("cclr_yes"))],
                 [("لا، ارجع للسلة", callbacks.build("cclr_no"))]])

    if cb.action == "cclr_yes":
        lines[:] = [ln for ln in lines if ln.get("status") != "ok"]
        ui.update(screen="cats")
        _save(db, session, ui)
        return MSG_CLEARED, _cats_rows(db, link, lines)

    if cb.action == "cclr_no":
        ui.update(screen="cart")
        _save(db, session, ui)
        return _cart_text(db, link, cat, lines), _cart_rows()

    if cb.action == "cok":
        if not _ok_lines(lines):
            ui.update(screen="cats")
            _save(db, session, ui)
            return MSG_CART_EMPTY, _cats_rows(db, link, lines)
        # same confirmation path as the text flow (validates, prices, creates the draft
        # order): draft lines are already in the shared shape.
        session.state = "confirming"
        _save(db, session, ui)
        text = menu_agent.handle_message(db, link, "تمام")
        return text, [[("🛒 طلب جديد", callbacks.build("cats"))]]

    if cb.action == "ccancel":
        reply = menu_agent.handle_message(db, link, "الغي")
        return reply.text, [[("🛒 طلب جديد", callbacks.build("cats"))]]

    return stale()


# ---------- custom-quantity text ----------

def handle_text(db: Session, link: TelegramLink, text: str
                ) -> tuple[str, list[list[tuple[str, str]]], int | None]:
    """A text message while await_qty is set: parse it as the custom quantity.
    Returns (text, rows, edit_id): edit_id is the stored menu message to edit."""
    cat = load_catalog(db, link.business_id)
    session, ui, lines = _ensure_session(db, link)
    item_id, variant_id = ui.get("item_id"), ui.get("variant_id")
    item = cat.items.get(item_id or "")
    variant = next((v for v in cat.variants.get(item_id or "", [])
                    if str(v.id) == (variant_id or "")), None)
    if item is None or variant is None:
        ui.update(await_qty=False, screen="cats")
        _save(db, session, ui)
        return MSG_STALE, _cats_rows(db, link, lines), ui.get("msg_id")
    probe = {"raw": text[:200], "qty": None, "item_id": item_id,
             "variant_id": variant_id, "candidates": [], "status": "need_qty",
             "reason": "button", "asked": 0}
    if not menu_agent._apply_qty(probe, text, item):
        unit = "بالكيلو (مثال: 2.5)" if item.sold_by == "weight" else "(مثال: 3)"
        return f"ما فهمتش الكمية. اكتب رقم {unit}:", [], ui.get("msg_id")
    lines.append(probe)
    ui.update(await_qty=False, screen="cart")
    _save(db, session, ui)
    return _cart_text(db, link, cat, lines), _cart_rows(), ui.get("msg_id")


def entry_rows(db: Session, link: TelegramLink) -> list[list[tuple[str, str]]]:
    """Read-only category keyboard (greeting/menu texts), without touching ui state."""
    session = _session(db, link)
    lines: list[dict] = []
    if session is not None and isinstance(session.draft, dict) \
            and session.draft.get("kind") == "menu":
        lines = session.draft.get("lines", [])
    return _cats_rows(db, link, lines)


def set_msg_id(db: Session, chat_id: int, message_id: int) -> None:
    """Worker hook: persist the bot's latest menu message id for later edits."""
    session = db.scalar(select(ChatSession).where(ChatSession.chat_id == chat_id))
    ui = _ui(session)
    if ui is None:
        return
    ui["msg_id"] = message_id
    _save(db, session, ui)
