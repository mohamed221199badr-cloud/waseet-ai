"""Checkout (WhatsApp spec 2026-11-26): no onboarding at /start; phone, delivery
type and address are collected at checkout after the cart confirm, then a final
summary sends the order to the cashier (status pending on main)."""

import pytest
from sqlalchemy import select

from app.chat import callbacks as cb_codec
from app.config import settings
from app.models_orders import Customer, Order
from app.telegram import service as svc
from tests.test_intake import shop  # noqa: F401
from tests.test_menu_buttons_v18 import CHAT, _flat, linked, menu, tap  # noqa: F401
from tests.test_migrations import _alembic, scratch_url  # noqa: F401

_ids_counter = [880000]


@pytest.fixture(autouse=True)
def _agent_on(monkeypatch):
    monkeypatch.setattr(settings, "chat_agent_enabled", True)
    monkeypatch.setattr(settings, "llm_mode", "off")


def _say(db, text):
    _ids_counter[0] += 1
    return svc.handle_update(db, {
        "update_id": _ids_counter[0],
        "message": {"message_id": _ids_counter[0], "text": text,
                    "chat": {"id": CHAT, "type": "private"},
                    "from": {"id": CHAT, "is_bot": False}}})


def _contact(db, phone="+201017597501", user_id=CHAT):
    _ids_counter[0] += 1
    return svc.handle_update(db, {
        "update_id": _ids_counter[0],
        "message": {"message_id": _ids_counter[0],
                    "chat": {"id": CHAT, "type": "private"},
                    "from": {"id": CHAT, "is_bot": False},
                    "contact": {"phone_number": phone, "user_id": user_id,
                                "first_name": "تجربة"}}})


def _customer(db, linked):  # noqa: F811
    return db.scalar(select(Customer).where(Customer.business_id == linked["ids"]["a"]))


@pytest.fixture()
def linked_new(db_session, linked):  # noqa: F811
    """The linked menu customer with an EMPTY profile, as a brand-new customer."""
    cust = _customer(db_session, linked)
    cust.phone, cust.address, cust.delivery_method = None, None, None
    db_session.commit()
    return linked


def _fill_cart(db, menu):  # noqa: F811
    """One كيلو of مفروم احمر via the button flow; ends on the cart screen."""
    tap(db, cb_codec.build("cat", "اللحوم", "p0"))
    tap(db, cb_codec.build("it", menu["meat"].id))
    tap(db, cb_codec.build("npd", 1))
    rep = tap(db, cb_codec.build("npc"))
    assert "✅ تأكيد الطلب" in _flat(rep.button_rows)
    return rep


def test_start_has_no_gate_for_new_customer(db_session, linked_new, menu):  # noqa: F811
    out = _say(db_session, "/start")
    rep = out[0]
    assert "أهلاً وسهلاً" in rep.text
    labels = _flat(rep.button_rows)
    assert any("اللحوم" in lbl for lbl in labels)
    assert "🔍 بحث" in labels and "🛒 السلة" in labels
    assert rep.reply_keyboard is None


def test_checkout_collects_phone_delivery_address(db_session, linked_new, menu):  # noqa: F811
    _fill_cart(db_session, menu)
    # phone missing: a fresh message with the native contact keyboard, not an edit
    rep = tap(db_session, cb_codec.build("cok"))
    assert "محتاجين رقمك" in rep.text
    assert rep.reply_keyboard[0][0]["request_contact"] is True
    assert rep.edit_message_id is None
    assert db_session.scalar(select(Order)) is None
    # contact share -> delivery type, contact keyboard removed
    rep = _contact(db_session)[0]
    assert "دليفري ولا استلام" in rep.text
    assert rep.remove_keyboard and rep.reply_keyboard is None
    assert _customer(db_session, linked_new).phone == "+201017597501"
    # delivery without a saved address asks for one
    rep = tap(db_session, cb_codec.build("ckdel", "delivery"))
    assert "اكتب العنوان" in rep.text and rep.edit_message_id == 42
    rep = _say(db_session, "شارع 9، المعادي، جنب الصيدلية")[0]
    assert "مراجعة الطلب النهائي" in rep.text
    assert "دليفري" in rep.text and "شارع 9" in rep.text
    assert "✅ تأكيد وإرسال للكاشير" in _flat(rep.button_rows)
    # final confirm: pending order, CRM persisted, cart cleared
    rep = tap(db_session, cb_codec.build("ckok"))
    assert "اتبعت للكاشير" in rep.text
    order = db_session.scalar(select(Order))
    assert order is not None and order.status == "pending"
    assert "دليفري" in (order.notes or "") and "شارع 9" in (order.notes or "")
    cust = _customer(db_session, linked_new)
    assert cust.delivery_method == "delivery" and cust.address.startswith("شارع 9")
    rep = tap(db_session, cb_codec.build("cart"))
    assert "فاضية" in rep.text


def test_pickup_skips_phone_and_address(db_session, linked, menu):  # noqa: F811
    _fill_cart(db_session, menu)
    rep = tap(db_session, cb_codec.build("cok"))
    assert "دليفري ولا استلام" in rep.text          # phone on file: skipped
    rep = tap(db_session, cb_codec.build("ckdel", "pickup"))
    assert "مراجعة الطلب النهائي" in rep.text
    assert "استلام من المحل" in rep.text and "العنوان" not in rep.text
    rep = tap(db_session, cb_codec.build("ckok"))
    assert "اتبعت للكاشير" in rep.text
    order = db_session.scalar(select(Order))
    assert order.status == "pending" and "استلام من المحل" in (order.notes or "")
    cust = _customer(db_session, linked)
    assert cust.delivery_method == "pickup"


def test_saved_address_reuse_and_change(db_session, linked, menu):  # noqa: F811
    cust = _customer(db_session, linked)
    cust.address = "عنوان قديم، الدقي"
    db_session.commit()
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("cok"))
    rep = tap(db_session, cb_codec.build("ckdel", "delivery"))
    assert "هتستلم على العنوان السابق؟" in rep.text and "عنوان قديم" in rep.text
    # keep: summary carries the saved address, CRM untouched
    rep = tap(db_session, cb_codec.build("ckaddr", "keep"))
    assert "مراجعة الطلب النهائي" in rep.text and "عنوان قديم" in rep.text
    tap(db_session, cb_codec.build("ckok"))
    assert _customer(db_session, linked).address == "عنوان قديم، الدقي"


def test_saved_address_change_updates_crm(db_session, linked, menu):  # noqa: F811
    cust = _customer(db_session, linked)
    cust.address = "عنوان قديم، الدقي"
    db_session.commit()
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("cok"))
    tap(db_session, cb_codec.build("ckdel", "delivery"))
    rep = tap(db_session, cb_codec.build("ckaddr", "change"))
    assert "اكتب العنوان" in rep.text
    rep = _say(db_session, "عنوان جديد، مدينة نصر")[0]
    assert "عنوان جديد" in rep.text
    tap(db_session, cb_codec.build("ckok"))
    assert _customer(db_session, linked).address == "عنوان جديد، مدينة نصر"


def test_typed_delivery_answers_work_without_buttons(db_session, linked, menu):  # noqa: F811
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("cok"))
    rep = _say(db_session, "من المحل")[0]
    assert "مراجعة الطلب النهائي" in rep.text and "استلام من المحل" in rep.text
    tap(db_session, cb_codec.build("ckok"))
    cust = _customer(db_session, linked)
    assert cust.delivery_method == "pickup"


def test_typed_delivery_then_address(db_session, linked, menu):  # noqa: F811
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("cok"))
    rep = _say(db_session, "توصيل")[0]
    assert "اكتب العنوان" in rep.text
    rep = _say(db_session, "شارع 9، المعادي")[0]
    assert "مراجعة الطلب النهائي" in rep.text and "شارع 9" in rep.text


def test_unrelated_text_reshows_step(db_session, linked, menu):  # noqa: F811
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("cok"))
    rep = _say(db_session, "اهلا")[0]
    assert "دليفري ولا استلام" in rep.text


def test_cancel_keeps_the_cart(db_session, linked, menu):  # noqa: F811
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("cok"))
    rep = tap(db_session, cb_codec.build("ckno"))
    assert "مفروم احمر" in rep.text and "✅ تأكيد الطلب" in _flat(rep.button_rows)
    # typed cancel during checkout also keeps the cart
    tap(db_session, cb_codec.build("cok"))
    rep = _say(db_session, "الغي")[0]
    assert "مفروم احمر" in rep.text
    # and checkout restarts cleanly afterwards
    rep = tap(db_session, cb_codec.build("cok"))
    assert "دليفري ولا استلام" in rep.text


def test_contact_ownership_and_bad_phone_rejected(db_session, linked_new, menu):  # noqa: F811
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("cok"))
    rep = _contact(db_session, user_id=CHAT + 1)[0]
    assert "مش رقم حد تاني" in rep.text
    assert _customer(db_session, linked_new).phone is None
    rep = _contact(db_session, phone="12")[0]
    assert "مش صح" in rep.text
    assert _customer(db_session, linked_new).phone is None


def test_short_address_rejected(db_session, linked, menu):  # noqa: F811
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("cok"))
    tap(db_session, cb_codec.build("ckdel", "delivery"))
    rep = _say(db_session, "abc")[0]
    assert "قصير" in rep.text
    assert db_session.scalar(select(Order)) is None


def test_empty_cart_confirm_does_not_start_checkout(db_session, linked, menu):  # noqa: F811
    rep = tap(db_session, cb_codec.build("cok"))
    assert "فاضية" in rep.text
    assert "دليفري ولا استلام" not in rep.text


def test_global_search_from_main_menu(db_session, linked, menu):  # noqa: F811
    out = _say(db_session, "/start")
    rep = out[0]
    assert "🔍 بحث" in _flat(rep.button_rows)
    rep = tap(db_session, cb_codec.build("srch"))
    assert "اكتب اسم الصنف" in rep.text
    rep = _say(db_session, "مفروم")[0]
    assert "نتائج البحث" in rep.text
    labels = _flat(rep.button_rows)
    assert any("مفروم احمر" in lbl and "اللحوم" in lbl for lbl in labels)
    # the result opens the item straight into the quantity flow
    data = [d for row in rep.button_rows for lbl, d in row if "مفروم" in lbl][0]
    rep = tap(db_session, data)
    assert "✅ تأكيد" in _flat(rep.button_rows)


def test_cart_qty_never_scientific_notation(db_session, linked, menu):  # noqa: F811
    # qty 10 (a float 10.0 in the draft) renders "10", never "1E+1" (19838529)
    tap(db_session, cb_codec.build("cat", "اللحوم", "p0"))
    tap(db_session, cb_codec.build("it", menu["meat"].id))
    tap(db_session, cb_codec.build("npd", 1))
    tap(db_session, cb_codec.build("npd", 0))
    rep = tap(db_session, cb_codec.build("npc"))
    assert "10 كيلو" in rep.text and "1E+1" not in rep.text


def test_global_search_no_results(db_session, linked, menu):  # noqa: F811
    tap(db_session, cb_codec.build("srch"))
    rep = _say(db_session, "سمك مشوي")[0]
    assert "مفيش نتائج" in rep.text


def test_typed_number_on_numpad_adds_to_cart(db_session, linked, menu):  # noqa: F811
    # the numpad screen is open: a typed number is the quantity (19817564)
    tap(db_session, cb_codec.build("cat", "اللحوم", "p0"))
    tap(db_session, cb_codec.build("it", menu["meat"].id))
    rep = _say(db_session, "2")[0]
    assert "مفروم احمر" in rep.text and "900" in rep.text  # 2 x 450 in the cart
    assert "✅ تأكيد الطلب" in _flat(rep.button_rows)


def test_bare_number_without_numpad_hints_pick_item(db_session, linked, menu):  # noqa: F811
    # no quantity screen open: a bare number gets a hint, not «مش فاهم قصدك» (19817564)
    rep = _say(db_session, "2")[0]
    assert "اختار الصنف الأول" in rep.text
    assert "مش فاهم" not in rep.text
    assert any("اللحوم" in lbl for lbl in _flat(rep.button_rows))
    # Arabic-Indic digits too
    rep = _say(db_session, "٢")[0]
    assert "اختار الصنف الأول" in rep.text
    # and on the cart screen (his actual state in the bug report)
    _fill_cart(db_session, menu)
    rep = _say(db_session, "12")[0]
    assert "اختار الصنف الأول" in rep.text


def test_confirm_with_same_variant_added_twice_creates_one_summed_line(
        db_session, linked, menu):  # noqa: F811
    """Prod bug 2026-11-26: the cart kept separate lines for one variant,
    order_lines is unique on (order, variant), the insert failed and confirm did
    nothing. Duplicate variants now merge into one line with the summed quantity."""
    from app.models_orders import OrderLine
    _fill_cart(db_session, menu)                      # 1 kg مفروم احمر
    tap(db_session, cb_codec.build("cadd"))
    tap(db_session, cb_codec.build("cat", "اللحوم", "p0"))
    tap(db_session, cb_codec.build("it", menu["meat"].id))
    tap(db_session, cb_codec.build("npd", 2))
    tap(db_session, cb_codec.build("npc"))            # same variant again, 2 kg
    tap(db_session, cb_codec.build("cok"))
    tap(db_session, cb_codec.build("ckdel", "pickup"))
    rep = tap(db_session, cb_codec.build("ckok"))
    assert "اتبعت للكاشير" in rep.text
    rows = db_session.scalars(select(OrderLine)).all()
    assert len(rows) == 1 and rows[0].quantity == 3


def test_post_order_screen_has_finish_button(db_session, linked, menu):  # noqa: F811
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("cok"))
    tap(db_session, cb_codec.build("ckdel", "pickup"))
    rep = tap(db_session, cb_codec.build("ckok"))
    labels = _flat(rep.button_rows)
    assert "✅ تمام، خلصت" in labels and "🛒 طلب جديد" in labels
    done = tap(db_session, cb_codec.build("done"))
    assert "شكراً ليك" in done.text
    assert "🛒 طلب جديد" in _flat(done.button_rows)
