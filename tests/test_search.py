"""Feature 3 (19758722): smart product search within a category.

Category-scoped, Arabic-normalized matching over the available catalog; results
ride the existing item-tap handler; state cleanup and cart preservation."""

from sqlalchemy import select

from app.chat import callbacks as cb_codec
from app.models_orders import ChatSession
from app.telegram import service as svc
from tests.test_intake import shop  # noqa: F401
from tests.test_menu_buttons_v18 import (  # noqa: F401
    CHAT,
    _add_item,
    _flat,
    linked,
    menu,
    tap,
)
from tests.test_migrations import _alembic, scratch_url  # noqa: F401

_ctr = [990000]


def _say(db, text):
    _ctr[0] += 1
    out = svc.handle_update(db, {
        "update_id": _ctr[0],
        "message": {"message_id": _ctr[0], "text": text,
                    "chat": {"id": CHAT, "type": "private"},
                    "from": {"id": CHAT, "is_bot": False}}})
    return out[0]


def _open_search(db):
    tap(db, cb_codec.build("cat", "اللحوم", "p0"))
    return tap(db, cb_codec.build("sc", "اللحوم", "p0"))


def test_search_button_on_category_screen(db_session, linked, menu):  # noqa: F811
    rep = tap(db_session, cb_codec.build("cat", "اللحوم", "p0"))
    datas = [d for row in rep.button_rows for _, d in row]
    assert "🔍 بحث عن صنف" in _flat(rep.button_rows)
    assert any(d.startswith("sc:اللحوم:") for d in datas)


def test_exact_and_partial_and_normalized_match(db_session, linked, menu):  # noqa: F811
    rep = _open_search(db_session)
    assert "اكتب اسم الصنف" in rep.text
    rep = _say(db_session, "مفروم احمر")      # exact
    assert "🔹 مفروم احمر" in _flat(rep.button_rows)
    rep = _say(db_session, "مفر")              # partial token
    assert "🔹 مفروم احمر" in _flat(rep.button_rows)
    rep = _say(db_session, "إحمر")             # alef/hamza normalization
    assert "🔹 مفروم احمر" in _flat(rep.button_rows)


def test_search_is_category_scoped(db_session, linked, menu):  # noqa: F811
    _open_search(db_session)
    rep = _say(db_session, "برجر")             # lives in مصنعات, not اللحوم
    assert "مفيش نتائج" in rep.text
    assert "🔙 رجوع للقسم" in _flat(rep.button_rows)
    # retry works: state survives a miss
    rep = _say(db_session, "مفروم")
    assert "🔹 مفروم احمر" in _flat(rep.button_rows)


def test_empty_query_reasks(db_session, linked, menu):  # noqa: F811
    _open_search(db_session)
    # punctuation-only normalizes to an empty query: no match, state kept for a retry
    rep = _say(db_session, "؟؟؟")
    assert "مفيش نتائج" in rep.text
    rep = _say(db_session, "مفروم")
    assert "🔹 مفروم احمر" in _flat(rep.button_rows)


def test_result_tap_enters_item_and_cart_preserved(db_session, linked, menu):  # noqa: F811
    # put a line in the cart first
    tap(db_session, cb_codec.build("it", menu["meat"].id))
    tap(db_session, cb_codec.build("npd", 1))
    tap(db_session, cb_codec.build("npc"))
    _open_search(db_session)
    rep = _say(db_session, "مفروم")
    data = [d for row in rep.button_rows for lbl, d in row if "مفروم احمر" in lbl][0]
    rep = tap(db_session, data)                # existing item handler
    assert "✅ تأكيد" in _flat(rep.button_rows)  # numpad for the found item
    tap(db_session, cb_codec.build("npd", 2))
    rep = tap(db_session, cb_codec.build("npc"))
    assert rep.text.count("مفروم احمر") == 2     # old line + new line: cart intact


def test_back_returns_to_category_and_clears_state(db_session, linked, menu):  # noqa: F811
    _open_search(db_session)
    rep = tap(db_session, cb_codec.build("cat", "اللحوم", "p0"))  # 🔙 رجوع للقسم
    assert "أصناف «اللحوم»" in rep.text
    session = db_session.scalar(select(ChatSession).where(ChatSession.chat_id == CHAT))
    assert session.draft["ui"].get("waiting_for_search_query") is False
    # text after leaving search is NOT consumed as a query: the normal text
    # handler answers "الأصناف" with the category listing, not search results
    rep = _say(db_session, "الأصناف")
    assert "الأقسام المتاحة" in rep.text


def test_forged_search_category_is_stale(db_session, linked, menu):  # noqa: F811
    rep = tap(db_session, cb_codec.build("sc", "قسم وهمي", "p0"))
    assert "مش متاح" in rep.text


def test_unavailable_items_never_match(db_session, linked, menu):  # noqa: F811
    hidden, _ = _add_item(db_session, linked["ids"]["a"], "اللحوم", "صنف مخفي",
                          [("عادي", __import__("decimal").Decimal("100.00"), True)])
    from app.models_menu import MenuItem
    db_session.get(MenuItem, hidden.id).is_available = False
    db_session.commit()
    _open_search(db_session)
    rep = _say(db_session, "مخفي")
    assert "مفيش نتائج" in rep.text
