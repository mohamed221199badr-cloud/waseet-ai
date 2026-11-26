"""Back-button audit. Every screen reached by a button (or a typed step inside a button
flow) must carry a 🔙 button, except an explicit allowlist: the welcome screen and the
post-order screen (user's rule), plus terminal cashier results documented below.

Two parts: a generic BFS over every reachable button, and scripted scenarios for the states a
BFS cannot reach by itself (non-empty cart, checkout, typed search, photo screens, cashier
notices). Every reply the bot makes is recorded; a missing back button fails the test."""

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.chat import callbacks as cb_codec
from app.config import settings
from app.models import Business
from app.models_orders import Customer, Order, TelegramLink
from app.telegram import service as svc
from tests.test_checkout import _fill_cart, _say  # noqa: F401
from tests.test_intake import shop  # noqa: F401
from tests.test_menu_buttons_v18 import (  # noqa: F401, F811
    CHAT,
    _add_category,
    _add_item,
    _cb,
    linked,
)
from tests.test_menu_buttons_v18 import menu as _menu  # noqa: F401
from tests.test_migrations import _alembic, scratch_url  # noqa: F401

BACK = "🔙"
# (text prefix, reason). Only these may lack a back button.
ALLOWED = (
    ("أهلاً وسهلاً", "welcome screen (user's rule)"),
    ("طلبك اتأكد", "post-order screen (user's rule)"),
    ("تمام ✅", "post-order screen, legacy wording"),
    ("شكراً ليك", "post-order 'done' screen (user's rule)"),
    ("✅ اتأكد", "cashier: final result of a decision, nothing to go back to"),
    ("❌ اترفض", "cashier: final result of a decision, nothing to go back to"),
)


@pytest.fixture(autouse=True)
def _agent_on(monkeypatch):
    monkeypatch.setattr(settings, "chat_agent_enabled", True)
    monkeypatch.setattr(settings, "llm_mode", "off")
    monkeypatch.setattr(settings, "order_auto_confirm", True)


class Walker:
    def __init__(self, db):
        self.db, self.n = db, 5_000_000
        self.seen: dict[str, object] = {}
        self.missing: dict[str, str] = {}

    def _record(self, what, reps):
        for rep in reps:
            if rep.chat_id != CHAT and not what.startswith("cashier"):
                continue
            if not (rep.text or rep.photo_file_id):
                continue
            labels = [lbl for row in (rep.button_rows or []) for lbl, _ in row] + \
                [lbl for lbl, _ in (rep.buttons or [])]
            key = f"{what} -> {(rep.text or '<photo>')[:36]!r}"
            self.seen[key] = rep
            if any(rep.text.startswith(p) for p, _ in ALLOWED):
                continue
            if not any(BACK in lbl for lbl in labels):
                self.missing[key] = " | ".join(labels) or "(no buttons)"

    def tap(self, data, chat=CHAT):
        self.n += 1
        reps = svc.handle_update(self.db, _cb(chat, data, 42))
        self._record(f"tap {data[:30]}", reps)
        return reps[0] if reps else None

    def say(self, text, chat=CHAT):
        self.n += 1
        reps = svc.handle_update(self.db, {
            "update_id": self.n,
            "message": {"message_id": self.n, "text": text,
                        "chat": {"id": chat, "type": "private"},
                        "from": {"id": chat, "is_bot": False, "first_name": "سامي"}}})
        self._record(f"say {text[:20]}", reps)
        return reps[0] if reps else None


def _datas(rep):
    return [d for row in (rep.button_rows or []) for _, d in row]


def _setup(db, shop_ids, _menu_):
    biz = shop_ids["ids"]["a"]
    _add_category(db, biz, "الفطائر", 5)
    _add_item(db, biz, "الفطائر", "فطيرة جبنة", [("M", Decimal("100"), True),
                                                ("L", Decimal("150"), False)])
    db.commit()
    return biz


def test_every_screen_has_a_back_button(db_session, linked, _menu):  # noqa: F811
    biz = _setup(db_session, linked, _menu)
    w = Walker(db_session)

    # ---- generic BFS over every button, repeated with a non-empty cart ----
    def bfs(start):
        queue, done = [start], set()
        while queue and len(done) < 150:
            data = queue.pop(0)
            if data in done:
                continue
            done.add(data)
            rep = w.tap(data)
            if rep is None:
                continue
            for d in _datas(rep):
                act = d.split(":")[0]
                if act not in ("noop", "ccancel", "cclr_yes", "ckok", "done", "oky",
                               "orr") and d not in done:
                    queue.append(d)

    bfs("home")
    # ---- scripted: weight item with a valid quantity, cart, clear, cancel ----
    w.tap(cb_codec.build("cat", "اللحوم", "p0"))
    w.tap(cb_codec.build("it", _menu["meat"].id))
    w.tap(cb_codec.build("npd", 2))
    w.tap(cb_codec.build("npdot"))
    w.tap(cb_codec.build("npd", 5))
    w.tap(cb_codec.build("npb"))
    w.tap(cb_codec.build("npc"))                       # cart
    w.tap(cb_codec.build("cadd"))
    w.tap(cb_codec.build("cart"))
    bfs(cb_codec.build("cart"))                        # cart + clear-confirm + checkout
    w.tap(cb_codec.build("cclr"))
    w.tap(cb_codec.build("cclr_no"))
    # ---- typed screens ----
    w.say("0")                                         # typed bad quantity / number
    w.tap(cb_codec.build("srch"))
    w.say("مفروم")                                     # global search results
    w.say("zzzz")                                      # no results
    w.tap(cb_codec.build("sc", "اللحوم", "p0"))
    w.say("مفروم")                                     # category search results
    w.say("zzzz")
    w.tap(cb_codec.build("sc", "مصنعات", "p0"))
    w.say("برجر")
    w.say("اهلا")                                      # greeting
    w.say("شكرا")
    w.say("مساعدة")
    w.say("كلام عشوائي مش مفهوم")
    # ---- stale / unknown button targets ----
    w.tap(cb_codec.build("it", "00000000-0000-0000-0000-000000000000"))
    w.tap(cb_codec.build("var", "00000000-0000-0000-0000-000000000000"))
    w.tap(cb_codec.build("cat", "مش موجود", "p0"))
    # ---- checkout: delivery needs an address, pickup, cancel, final ----
    w.tap(cb_codec.build("cart"))
    w.tap(cb_codec.build("cok"))
    w.tap(cb_codec.build("ckgo"))
    w.tap(cb_codec.build("ckdel", "delivery"))
    w.say("ab")                                        # address too short
    w.say("شارع 9، المعادي، جنب الصيدلية")
    w.tap(cb_codec.build("ckno"))
    w.tap(cb_codec.build("cok"))
    w.tap(cb_codec.build("ckgo"))
    w.tap(cb_codec.build("ckdel", "pickup"))
    w.tap(cb_codec.build("ckok"))                      # post-order (allowed)
    w.tap(cb_codec.build("done"))
    w.tap(cb_codec.build("cats"))
    w.tap(cb_codec.build("ccancel"))
    # ---- photo screens ----
    biz_row = db_session.get(Business, biz)
    biz_row.menu_photo_file_id = "brochure-id"
    db_session.commit()
    w.tap(cb_codec.build("cats"))                      # brochure + categories
    w.tap(cb_codec.build("showmenu"))                  # official-menu photo screen
    w.tap(cb_codec.build("home"))
    biz_row.menu_photo_file_id = None
    db_session.commit()
    w.tap(cb_codec.build("showmenu"))                  # no photo: owner upload prompt / soon
    from app.models_menu import MenuCategory
    cat = db_session.scalar(select(MenuCategory).where(MenuCategory.name == "الفطائر"))
    if hasattr(cat, "photo_file_id"):
        cat.photo_file_id = "cat-photo"
        db_session.commit()
        w.tap(cb_codec.build("cat", "الفطائر", "p0"))  # photo-only category page
    print(f"\nSCREENS WALKED: {len(w.seen)}")
    for k in w.seen:
        print("   ", k)
    for k, v in w.missing.items():
        print("MISSING BACK:", k, "| buttons:", v)
    assert not w.missing, "\n".join(f"{k}  buttons: {v}" for k, v in w.missing.items())


def test_cashier_screens_have_back_where_a_parent_exists(db_session, linked, _menu, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "order_auto_confirm", False)
    biz = linked["ids"]["a"]
    staff = Customer(business_id=biz, name="كاشير", telegram_chat_id=777001)
    db_session.add(staff)
    db_session.flush()
    db_session.add(TelegramLink(business_id=biz, customer_id=staff.id, chat_id=777001,
                                is_owner=True))
    db_session.commit()
    w = Walker(db_session)
    _fill_cart(db_session, _menu)
    w.tap(cb_codec.build("cok"))
    w.tap(cb_codec.build("ckgo"))
    w.tap(cb_codec.build("ckdel", "pickup"))
    reps = svc.handle_update(db_session, _cb(CHAT, cb_codec.build("ckok"), 42))
    notice = next(r for r in reps if r.chat_id == 777001)
    order = db_session.scalar(select(Order))
    w.n += 1
    for data in (cb_codec.build("orj", order.id), cb_codec.build("orb", order.id)):
        w.tap(data, chat=777001)
    assert notice.button_rows  # the notice itself is the root screen of the cashier flow
    reasons = w.tap(cb_codec.build("orj", order.id), chat=777001)
    assert any(BACK in lbl for row in reasons.button_rows for lbl, _ in row)
    assert not [k for k in w.missing if k.startswith("tap orj") or k.startswith("tap orb")], \
        w.missing


def test_phone_step_has_a_back_button_and_it_returns_to_the_cart(db_session, linked, _menu):  # noqa: F811
    cust = db_session.scalar(select(Customer).where(Customer.business_id == linked["ids"]["a"]))
    cust.phone = None
    db_session.commit()
    w = Walker(db_session)
    _fill_cart(db_session, _menu)
    w.tap(cb_codec.build("cok"))
    rep = w.tap(cb_codec.build("ckgo"))
    assert "محتاجين رقمك" in rep.text
    assert any(btn.get("text") == "🔙 رجوع" for row in rep.reply_keyboard for btn in row)
    back = w.say("🔙 رجوع")
    assert "✅ تأكيد الطلب" in [lbl for row in back.button_rows for lbl, _ in row]
    assert back.remove_keyboard
    # the checkout step is gone: typing a phone-like text is not captured by checkout
    from app.chat import checkout
    assert not checkout.active(db_session, db_session.scalar(select(TelegramLink)))


def test_back_from_checkout_screen_leaves_checkout(db_session, linked, _menu):  # noqa: F811
    from app.chat import checkout
    w = Walker(db_session)
    _fill_cart(db_session, _menu)
    w.tap(cb_codec.build("cok"))
    w.tap(cb_codec.build("ckgo"))
    w.tap(cb_codec.build("ckdel", "delivery"))        # address asked, checkout step open
    link = db_session.scalar(select(TelegramLink).where(TelegramLink.chat_id == CHAT))
    assert checkout.active(db_session, link)
    w.tap(cb_codec.build("cart"))                      # the 🔙 on the address screen
    assert not checkout.active(db_session, link)
