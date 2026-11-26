"""UI v1.8: fully button-driven menu ordering (menu mode only).

Each screen is a function over (db, link) returning (text, button_rows). Button taps
arrive as codec Callbacks; quantities are entered on the in-chat numpad.
Everything writes the SAME draft lines the text flow uses (draft kind "menu"), so
confirmation and order creation stay in menu_agent - no duplicated business logic,
no LLM anywhere in this flow.

Session ui state (inside ChatSession.draft["ui"]):
    screen: cats | items | item | qty | cart | clear_confirm
    category, page, item_id, variant_id: the current selection
    current_input: the in-chat numpad's pending quantity text (qty screen)
    waiting_for_search_query, search_category, search_page: in-category search (F3)
    msg_id: the bot's last menu message, for edit-in-place navigation
"""

import re
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from app.audit import write_audit
from app.chat import agent as legacy
from app.chat import callbacks, menu_agent
from app.chat.menu_catalog import (
    category_items,
    list_categories,
    load_catalog,
    resolve_variant_price,
)
from app.matching.normalize import normalize_digits
from app.models import Business
from app.models_menu import MenuCategory
from app.models_orders import ChatSession, TelegramLink

PAGE_SIZE = 6

MSG_STALE = "الاختيار ده مش متاح دلوقتي. اختار من الأصناف:"
MSG_CATS = "اختار من الأصناف:"
MSG_CLEARED = "اتفضت السلة. نبدأ من الأول:"
MSG_CART_EMPTY = "السلة فاضية لسه. اختار من الأصناف:"
MSG_SEARCH_ASK = "🔍 اكتب اسم الصنف اللي بتدور عليه."
MSG_SEARCH_NONE = "مفيش نتائج لـ «{query}» في قسم «{category}». جرب اسم تاني."
MSG_SEARCH_NONE_GLOBAL = "مفيش نتائج لـ «{query}». جرب اسم تاني."
MSG_PICK_ITEM_FIRST = ("الرقم ده كمية، بس محتاج تعرف لأي صنف 🙂\n"
                       "اختار الصنف الأول من الأصناف تحت، وبعدها اكتب أو اضغط الكمية.")
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
    return bool(ui and (ui.get("await_qty") or ui.get("waiting_for_search_query")))


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

_CATEGORY_EMOJI = {
    "لحم": "🥩", "لحوم": "🥩", "ضاني": "🐑", "فراخ": "🍗", "برجر": "🍔",
    "حلو": "🍬", "خدمات": "🛠", "مصنعات": "🌭", "قطع": "🥩",
}


def _category_emoji(name: str) -> str:
    return next((e for key, e in _CATEGORY_EMOJI.items() if key in name), "🍽")


def _cats_rows(db: Session, link: TelegramLink, lines: list[dict]) -> list[list[tuple[str, str]]]:
    rows = [[(f"{_category_emoji(c.name)} {c.name}",
              callbacks.build("cat", c.name, "p0"))]
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
        [(f"🔹 {i.name}", callbacks.build("it", i.id))] for i in chunk
    ]
    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(("◀ السابق", callbacks.build("cat", category, f"p{page - 1}")))
        nav.append((f"{page + 1}/{total_pages}", callbacks.build("noop")))
        if page < total_pages - 1:
            nav.append(("التالي ▶", callbacks.build("cat", category, f"p{page + 1}")))
        rows.append(nav)
    rows.append([("🔍 بحث عن صنف", callbacks.build("sc", category, f"p{page}"))])
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


def _numpad_rows(item, item_id: str) -> list[list[tuple[str, str]]]:
    """Interactive in-chat numeric keypad (spec 19758722, feature 1).

    Weight items get a decimal point (the only layout adjustment: the confirm row
    moves down one). Unit items keep the original 3-button bottom row."""
    rows = [[(str(n), callbacks.build("npd", n)) for n in chunk]
            for chunk in ((1, 2, 3), (4, 5, 6), (7, 8, 9))]
    if item.sold_by == "weight":
        rows.append([(".", callbacks.build("npdot")),
                     ("0", callbacks.build("npd", 0)),
                     ("⌫ مسح", callbacks.build("npb"))])
        rows.append([("✅ تأكيد", callbacks.build("npc"))])
    else:
        rows.append([("⌫ مسح", callbacks.build("npb")),
                     ("0", callbacks.build("npd", 0)),
                     ("✅ تأكيد", callbacks.build("npc"))])
    rows.append([("🔙 رجوع", callbacks.build("it", item_id))])
    return rows


def _qty_prompt(item, current_input: str) -> str:
    unit = "كيلو" if item.sold_by == "weight" else ""
    shown = current_input if current_input else "…"
    return (f"كام «{item.name}» عايز؟ {unit}".strip() + f":\n▫️ {shown}")


def _qty_invalid(reason: str, item, item_id: str, current_input: str):
    return (f"{reason}\n" + _qty_prompt(item, current_input),
            _numpad_rows(item, item_id))


def _parse_numpad_qty(item, raw: str) -> Decimal | None:
    """Deterministic validation against the existing sold_by rules. None = invalid."""
    if not raw or raw == ".":
        return None
    try:
        qty = Decimal(raw)
    except ArithmeticError:
        return None
    if qty <= 0 or qty > 100000:  # same bounds as menu_agent._apply_qty
        return None
    if item.sold_by == "weight":
        if -qty.as_tuple().exponent > 3:  # Numeric(10, 3): max 3 decimal places
            return None
    elif qty != qty.to_integral_value():
        return None  # unit items: whole numbers only
    if item.min_quantity is not None and qty < item.min_quantity:
        return None
    return qty


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
        q = menu_agent._qty(qty)  # never exponent notation (10.0 -> "10", not "1E+1")
        qty_txt = f"{q} كيلو" if item.sold_by == "weight" else q
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

def _navigate(db: Session, link: TelegramLink, cb: callbacks.Callback,
              msg_id: int | None) -> tuple[str, list[list[tuple[str, str]]]]:
    """One navigation tap -> one screen. msg_id is the tapped message (edit target)."""
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
        ui.update(screen="cats", waiting_for_search_query=False)
        _save(db, session, ui)
        return MSG_CATS, _cats_rows(db, link, lines)

    if cb.action == "cat":
        category, page = cb.args[0], int(cb.args[1][1:])
        if not category_items(cat, category):
            return stale()
        rows, page, _ = _items_rows(cat, category, page)
        ui.update(screen="items", category=category, page=page,
                  waiting_for_search_query=False)
        _save(db, session, ui)
        return f"أصناف «{category}»:", rows

    if cb.action == "sc":
        category, page = cb.args[0], int(cb.args[1][1:])
        if not category_items(cat, category):
            return stale()
        ui.update(screen="search", search_category=category, search_page=page,
                  waiting_for_search_query=True)
        _save(db, session, ui)
        return MSG_SEARCH_ASK, [[("🔙 رجوع للقسم",
                                  callbacks.build("cat", category, f"p{page}"))]]

    if cb.action == "srch":
        # global search from the main menu: no category scope, back to the menu
        ui.update(screen="search", search_category=None, search_page=0,
                  waiting_for_search_query=True)
        _save(db, session, ui)
        return MSG_SEARCH_ASK, [[("🔙 الأصناف", callbacks.build("cats"))]]

    if cb.action == "it":
        item_id = cb.args[0]
        item = cat.items.get(item_id)
        if item is None:
            return stale()
        variants = cat.variants.get(item_id, [])
        if not variants:
            return stale()
        category = item.category or ui.get("category", "")
        page = int(ui.get("page", 0))
        if len(variants) > 1:
            ui.update(screen="item", item_id=item_id, category=category,
                      waiting_for_search_query=False)
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
        ui.update(screen="qty", item_id=item_id, variant_id=str(variant.id),
                  current_input="", waiting_for_search_query=False, await_qty=True)
        _save(db, session, ui)
        return _qty_prompt(item, ""), _numpad_rows(item, item_id)

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
        ui.update(screen="qty", item_id=item_id, variant_id=variant_id,
                  current_input="", waiting_for_search_query=False, await_qty=True)
        _save(db, session, ui)
        return _qty_prompt(item, ""), _numpad_rows(item, item_id)

    if cb.action in ("npd", "npdot", "npb", "npc"):
        if ui.get("screen") != "qty":
            return stale()  # repeated/late taps on an old keypad never add twice
        item_id, variant_id = ui.get("item_id"), ui.get("variant_id")
        item = cat.items.get(item_id or "")
        variant = next((v for v in cat.variants.get(item_id or "", [])
                        if str(v.id) == (variant_id or "")), None)
        if item is None or variant is None:
            return stale()
        current = ui.get("current_input") or ""
        if cb.action == "npd":
            if len(current) < 8:
                current += cb.args[0]
            stripped = current.lstrip("0")
            if stripped.startswith("."):
                stripped = "0" + stripped
            current = stripped or ("0" if current else "")
        elif cb.action == "npdot":
            if item.sold_by == "weight" and "." not in current:
                current = (current or "0") + "."
            # unit items have no decimal button: a forged tap is ignored, keypad kept
        elif cb.action == "npb":
            current = current[:-1]
        else:  # npc: confirm
            qty = _parse_numpad_qty(item, current)
            if qty is None:
                reason = "اكتب الكمية الأول." if not current else "الكمية دي مش صح."
                if current and item.sold_by == "unit":
                    try:
                        if Decimal(current) != Decimal(current).to_integral_value():
                            reason = "الصنف ده بيتباع بالقطعة، اكتب رقم صحيح."
                    except ArithmeticError:
                        pass
                if current and item.min_quantity is not None:
                    try:
                        if Decimal(current) > 0 and Decimal(current) < item.min_quantity:
                            reason = f"أقل كمية {menu_agent._money(item.min_quantity)}."
                    except ArithmeticError:
                        pass
                ui["current_input"] = current
                _save(db, session, ui)
                return _qty_invalid(reason, item, item_id, current)
            lines.append({"raw": item.name[:200], "qty": float(qty), "item_id": item_id,
                          "variant_id": variant_id, "candidates": [], "status": "ok",
                          "reason": "button", "asked": 0})
            ui.update(screen="cart", current_input="")
            _save(db, session, ui)
            return _cart_text(db, link, cat, lines), _cart_rows()
        ui["current_input"] = current
        _save(db, session, ui)
        return _qty_prompt(item, current), _numpad_rows(item, item_id)

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

    if cb.action == "ccancel":
        reply = menu_agent.handle_message(db, link, "الغي")
        return reply.text, [[("🛒 طلب جديد", callbacks.build("cats"))]]

    return stale()


# ---------- custom-quantity text ----------

SEARCH_MAX_RESULTS = 10


def _search_items(cat, category: str, query: str):
    """Category-scoped match on the normalized names (alef/ya/ta-marbuta/diacritics/
    digits all unified by the existing engine). A query matches when every token
    appears in the item name, or the whole query is a substring of it."""
    from app.matching.normalize import normalize
    q = normalize(query)
    if not q:
        return []
    tokens = q.split()
    out = []
    for item in category_items(cat, category):  # the catalog holds available items only
        name = normalize(item.name)
        name_tokens = name.split()
        if q in name or all(any(t in nt for nt in name_tokens) for t in tokens):
            out.append(item)
    return out


def _handle_search(db: Session, session: ChatSession, ui: dict, cat, text: str):
    category = ui.get("search_category") or ""
    page = int(ui.get("search_page") or 0)
    if category:
        back = [[("🔙 رجوع للقسم", callbacks.build("cat", category, f"p{page}"))]]
    else:
        back = [[("🔙 الأصناف", callbacks.build("cats"))]]
    query = text.strip()
    if not query:
        return MSG_SEARCH_ASK, back, ui.get("msg_id")
    if category:
        matches = [(i, category) for i in _search_items(cat, category, query)]
    else:
        # global (main-menu) search: every category, labeled in the buttons
        matches = []
        for c in list_categories(db, session.business_id):
            matches += [(i, c.name) for i in _search_items(cat, c.name, query)]
    if not matches:
        # stay in the search state so the next text retries
        _save(db, session, ui)
        if category:
            return MSG_SEARCH_NONE.format(query=query[:100], category=category), \
                back, ui.get("msg_id")
        return MSG_SEARCH_NONE_GLOBAL.format(query=query[:100]), back, ui.get("msg_id")
    rows = []
    for i, cname in matches[:SEARCH_MAX_RESULTS]:
        label = f"🔹 {i.name}" if category else f"🔹 {i.name} · {cname}"
        rows.append([(label, callbacks.build("it", i.id))])
    rows.append(back[0])
    if category:
        shown = f"نتائج البحث عن «{query[:100]}» في «{category}»:"
    else:
        shown = f"نتائج البحث عن «{query[:100]}»:"
    if len(matches) > SEARCH_MAX_RESULTS:
        shown += f" (أول {SEARCH_MAX_RESULTS} من {len(matches)})"
    _save(db, session, ui)
    return shown, rows, ui.get("msg_id")


def handle_text(db: Session, link: TelegramLink, text: str
                ) -> tuple[str, list[list[tuple[str, str]]], int | None]:
    """A text message while await_qty is set: parse it as the custom quantity.
    Returns (text, rows, edit_id): edit_id is the stored menu message to edit."""
    cat = load_catalog(db, link.business_id)
    session, ui, lines = _ensure_session(db, link)
    if ui.get("waiting_for_search_query"):
        return _handle_search(db, session, ui, cat, text)
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


def is_bare_number(text: str) -> bool:
    """A lone quantity (Arabic-Indic safe) with no item context (bug 19817564)."""
    t = normalize_digits(text).strip()
    return bool(re.fullmatch(r"\d+(\.\d+)?", t))


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


# ---------- section 3.0: welcome + menu discovery ----------

MSG_WELCOME = ("أهلاً وسهلاً بيك ❤️\n"
               "نورتنا! إحنا سعداء بخدمتك.\n"
               "تحب تطلب إيه النهارده؟")
MSG_ASK_MENU_IMAGE = ("تحب تبعتلنا صورة المنيو؟ 📸\n"
                      "ابعت صورة المنيو هنا علشان نقدر نساعدك في اختيار طلبك.")
MSG_MENU_IMAGE_SAVED = "وصلتنا صورة المنيو، شكراً ليك ❤️"
MSG_MENU_SOON = "المنيو هيتاح هنا قريب، جرب تاني بعد شوية."
MSG_MENU_PHOTO_OWNER_ONLY = "صورة المنيو الرسمية بيبعتها صاحب المحل بس."
# shop contact details confirmed by the owner (message 19667742); constants until
# a business-settings surface exists
SHOP_ADDRESS = "الشرقية، مركز منيا القمح، منتصف شارع جمعية المحافظة على القرآن الكريم"
SHOP_PHONES = ("01017597501", "01040756249")
SHOP_WHATSAPP = "01553799899"
MSG_FINISHED = ("شكراً ليك 🙏 طلبك عند الكاشير وهيتواصلوا معاك قريب.\n"
                "لو عايز تطلب تاني دوس «طلب جديد».")
MSG_CONTACT = (f"📞 تواصل معنا:\n"
               f"العنوان: {SHOP_ADDRESS}\n"
               f"تليفون: {' / '.join(SHOP_PHONES)}\n"
               f"واتساب: {SHOP_WHATSAPP}")


def main_menu(db: Session, link: TelegramLink) -> tuple[str, list[list[tuple[str, str]]]]:
    """The /start entry point: welcome text with the main menu directly - categories,
    global search and the cart (checkout spec 2026-11-26: no onboarding gate). The
    menu-photo and contact rows stay from spec 3.0A."""
    session, ui, lines = _ensure_session(db, link)
    ui.update(screen="cats", await_qty=False, await_menu_image=False)
    _save(db, session, ui)
    rows = _cats_rows(db, link, lines)
    if not any(data == callbacks.build("cart") for row in rows for _, data in row):
        rows.append([("🛒 السلة", callbacks.build("cart"))])
    rows.append([("🔍 بحث", callbacks.build("srch"))])
    rows.append([("📋 عرض المنيو", callbacks.build("showmenu")),
                 ("📞 تواصل معنا", callbacks.build("contact"))])
    return MSG_WELCOME, rows


def welcome(db: Session, link: TelegramLink) -> tuple[str, list[list[tuple[str, str]]]]:
    """The /start entry point in menu mode (spec 3.0A)."""
    session, ui, _lines = _ensure_session(db, link)
    ui.update(screen="welcome", await_qty=False, await_menu_image=False)
    _save(db, session, ui)
    return MSG_WELCOME, [
        [("🛒 ابدأ الطلب", callbacks.build("cats"))],
        [("📋 عرض المنيو", callbacks.build("showmenu"))],
        [("📞 تواصل معنا", callbacks.build("contact"))],
    ]


def handle_callback(db: Session, link: TelegramLink, cb: callbacks.Callback,
                    msg_id: int | None
                    ) -> tuple[str, list[list[tuple[str, str]]], str | None]:
    """One button tap -> (text, button_rows, photo_file_id or None)."""
    if cb.action in ("showmenu", "contact"):
        session, ui, _lines = _ensure_session(db, link)
        ui["await_qty"] = False
        if msg_id is not None:
            ui["msg_id"] = msg_id
        if cb.action == "contact":
            _save(db, session, ui)
            return MSG_CONTACT, [[("🔙 رجوع", callbacks.build("cats"))]], None
        business = db.get(Business, link.business_id)
        file_id = business.menu_photo_file_id if business is not None else None
        if file_id:
            # an official menu image exists: send it, never ask for an upload
            ui["await_menu_image"] = False
            _save(db, session, ui)
            return "", [[("🛒 ابدأ الطلب", callbacks.build("cats"))]], file_id
        if not link.is_owner:
            # only the owner's chat may set the official menu (19673006): never
            # ask a customer for an upload
            _save(db, session, ui)
            return MSG_MENU_SOON, [[("🛒 ابدأ الطلب", callbacks.build("cats"))]], None
        ui["await_menu_image"] = True
        _save(db, session, ui)
        return MSG_ASK_MENU_IMAGE, [], None
    if cb.action == "done":
        # post-order "finish" button: a friendly close, keeps the new-order option
        return MSG_FINISHED, [[("🛒 طلب جديد", callbacks.build("cats"))]], None
    text, rows = _navigate(db, link, cb, msg_id)
    if cb.action == "cat" and text == f"أصناف «{cb.args[0]}»:":
        # a category with an owner-uploaded page photo (m014) opens as one photo
        # message: the photo, the items header as caption, the buttons under it
        photo = _category_photo(db, link.business_id, cb.args[0])
        if photo:
            return text, rows, photo
    return text, rows, None


def wants_photo(db: Session, link: TelegramLink) -> bool:
    """True while the customer was asked for a menu image (waiting_for_menu_image)."""
    ui = _ui(_session(db, link))
    return bool(ui and ui.get("await_menu_image"))


def handle_photo(db: Session, link: TelegramLink, file_id: str
                 ) -> tuple[str, list[list[tuple[str, str]]]]:
    """A photo while await_menu_image is set: store the reference on the business.
    Only the Telegram file_id is kept (Telegram serves the bytes); the largest
    photo size is chosen by the caller. uploaded_by records which chat sent it.
    Owner links only (19673006): a non-owner photo never becomes the menu."""
    if not link.is_owner:
        return MSG_MENU_PHOTO_OWNER_ONLY, []
    business = db.get(Business, link.business_id)
    if business is not None:
        business.menu_photo_file_id = file_id[:200]
        business.menu_photo_uploaded_by = link.chat_id
        business.menu_photo_uploaded_at = legacy._now()
        db.add(business)
    session, ui, _lines = _ensure_session(db, link)
    ui.update(await_menu_image=False, screen="cats")
    _save(db, session, ui)
    return MSG_MENU_IMAGE_SAVED, [[("🛒 ابدأ الطلب", callbacks.build("cats"))]]


# ---------- per-category page photos (m014, WhatsApp spec 2026-11-26) ----------
MSG_CATEGORY_PHOTO_SAVED = "تمام ✅ صورة قسم «{category}» اتحفظت. هتظهر لأي زبون يفتح القسم."
MSG_CATEGORY_PHOTO_NONE = "مفيش قسم بالاسم ده. الأقسام المتاحة:\n{categories}"


def _category_photo(db: Session, business_id, category: str) -> str | None:
    """The stored page photo for one category, if the owner uploaded one."""
    row = db.scalar(select(MenuCategory).where(
        MenuCategory.business_id == business_id, MenuCategory.name == category))
    return row.photo_file_id if row is not None else None


def handle_category_photo(db: Session, link: TelegramLink, caption: str, file_id: str
                          ) -> tuple[str, list[list[tuple[str, str]]]]:
    """An owner photo whose caption is a category name: save it as that category's
    page photo. Exact normalized name match only - never a fuzzy guess on his data.
    Owner links only (19673006); the caller checks is_owner before calling."""
    from app.matching.normalize import normalize
    target = normalize(caption)
    match = next((c for c in list_categories(db, link.business_id)
                  if normalize(c.name) == target and target), None)
    if match is None:
        names = "\n".join(f"- {c.name}" for c in list_categories(db, link.business_id))
        return MSG_CATEGORY_PHOTO_NONE.format(categories=names or "(لسه مفيش أقسام)"), []
    match.photo_file_id = file_id[:200]
    db.add(match)
    write_audit(db, business_id=link.business_id, entity="menu_category",
                entity_id=str(match.id), action="photo_set",
                payload={"category": match.name, "by": "owner_chat",
                         "chat_id": link.chat_id})
    return MSG_CATEGORY_PHOTO_SAVED.format(category=match.name), []
