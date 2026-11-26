"""UI v1.8: fully button-driven menu ordering over the codec (no LLM, no network).

Covers the end-to-end spec scenario: category -> item -> variant -> quick quantity ->
cart -> confirm; pagination at 6 per page; custom-quantity state; cart clear with
confirmation; edit-in-place reply fields; stale/forged callbacks; legacy-business
isolation from codec callbacks."""

import itertools
import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.chat import callbacks as cb_codec
from app.config import settings
from app.models import Business
from app.models_menu import MenuCategory, MenuItem, MenuItemVariant
from app.models_orders import Order
from app.telegram import service as svc
from tests.test_intake import shop  # noqa: F401
from tests.test_migrations import _alembic, scratch_url  # noqa: F401
from tests.test_telegram import link

_ids = itertools.count(770000)
CHAT = 700


@pytest.fixture(autouse=True)
def _agent_on(monkeypatch):
    monkeypatch.setattr(settings, "chat_agent_enabled", True)
    monkeypatch.setattr(settings, "llm_mode", "off")


@pytest.fixture()
def linked(client, db_session, shop):  # noqa: F811
    link(db_session, CHAT, shop)
    # onboarding (spec 19758722 F2) gates menu flows on a complete profile; the shop
    # customer has a phone, so pickup completes it and the flow tests stay ungated
    from app.models_orders import Customer
    cust = db_session.scalar(
        select(Customer).where(Customer.business_id == shop["ids"]["a"]))
    cust.delivery_method = "pickup"
    db_session.commit()
    return shop


def _add_category(db, biz, name, sort=0):
    c = MenuCategory(business_id=biz, name=name, sort_order=sort)
    db.add(c)
    db.flush()
    return c


def _add_item(db, biz, category, name, variants, sold_by="unit", unit_label=None):
    item = MenuItem(business_id=biz, category=category, name=name, kind="food",
                    sold_by=sold_by, unit_label=unit_label)
    db.add(item)
    db.flush()
    vs = []
    for i, (vname, price, is_default) in enumerate(variants):
        v = MenuItemVariant(business_id=biz, item_id=item.id, name=vname, price=price,
                            is_default=is_default, sort_order=i)
        db.add(v)
        vs.append(v)
    db.commit()
    return item, vs


@pytest.fixture()
def menu(db_session, shop):  # noqa: F811
    biz = shop["ids"]["a"]
    _add_category(db_session, biz, "اللحوم", 0)
    _add_category(db_session, biz, "مصنعات", 1)
    meat, meat_v = _add_item(db_session, biz, "اللحوم", "مفروم احمر",
                             [("عادي", Decimal("450.00"), True)],
                             sold_by="weight", unit_label="كيلو")
    burger, burger_v = _add_item(db_session, biz, "مصنعات", "برجر بلدي",
                                 [("سعر 1", Decimal("280.00"), True),
                                  ("سعر 2", Decimal("350.00"), False)])
    return {"meat": meat, "meat_v": meat_v, "burger": burger, "burger_v": burger_v}


def _cb(chat_id, data, msg_id=42):
    return {
        "update_id": next(_ids),
        "callback_query": {
            "id": f"cbq-{next(_ids)}", "data": data,
            "from": {"id": chat_id, "is_bot": False},
            "message": {"message_id": msg_id,
                        "chat": {"id": chat_id, "type": "private"}},
        },
    }


def tap(db, data, msg_id=42):
    out = svc.handle_update(db, _cb(CHAT, data, msg_id))
    assert out, f"no reply for {data}"
    return out[0]


def _flat(rows):
    return [label for row in (rows or []) for label, _ in row]


def _datas(rows):
    return [data for row in (rows or []) for _, data in row]


def test_greeting_carries_category_keyboard(db_session, linked, menu):  # noqa: F811
    out = svc.handle_update(db_session, {
        "update_id": next(_ids),
        "message": {"message_id": 1, "text": "اهلا",
                    "chat": {"id": CHAT, "type": "private"},
                    "from": {"id": CHAT, "is_bot": False}}})
    labels = _flat(out[0].button_rows)
    assert any("اللحوم" in lbl for lbl in labels) and any("مصنعات" in lbl for lbl in labels)
    assert any(lbl.startswith("🥩") for lbl in labels)  # emoji next to options (19677637)


def test_full_button_flow_to_draft_order(db_session, linked, menu):  # noqa: F811
    # step 1: category tap edits the tapped message and lists the category's items
    rep = tap(db_session, cb_codec.build("cat", "اللحوم", "p0"))
    assert rep.edit_message_id == 42 and rep.store_msg_id
    assert any("مفروم احمر" in lbl for lbl in _flat(rep.button_rows))
    # step 2: single-variant weight item goes straight to the numeric keypad
    rep = tap(db_session, cb_codec.build("it", menu["meat"].id))
    labels = _flat(rep.button_rows)
    assert all(str(n) in labels for n in range(10)) and "✅ تأكيد" in labels
    assert "." in labels  # weight items get the decimal point
    # step 3: keypad entry (1 كيلو) adds the line and shows the cart with subtotal
    tap(db_session, cb_codec.build("npd", 1))
    rep = tap(db_session, cb_codec.build("npc"))
    assert "مفروم احمر" in rep.text and "450" in rep.text
    assert "✅ تأكيد الطلب" in _flat(rep.button_rows)
    # step 4: confirm starts checkout (phone is on file -> delivery type first);
    # pickup skips the address, final confirm sends the order to the cashier
    rep = tap(db_session, cb_codec.build("cok"))
    assert "دليفري ولا استلام" in rep.text and rep.edit_message_id == 42
    assert db_session.scalar(select(Order)) is None
    rep = tap(db_session, cb_codec.build("ckdel", "pickup"))
    assert "مراجعة الطلب النهائي" in rep.text and "استلام من المحل" in rep.text
    assert "✅ تأكيد وإرسال للكاشير" in _flat(rep.button_rows)
    rep = tap(db_session, cb_codec.build("ckok"))
    order = db_session.scalar(select(Order).order_by(Order.created_at.desc()))
    assert order is not None and order.status == "pending"
    assert "اتبعت للكاشير" in rep.text


def test_multi_variant_item_shows_variant_buttons(db_session, linked, menu):  # noqa: F811
    rep = tap(db_session, cb_codec.build("cat", "مصنعات", "p0"))
    rep = tap(db_session, cb_codec.build("it", menu["burger"].id))
    labels = _flat(rep.button_rows)
    assert any("سعر 1" in lbl and "280" in lbl for lbl in labels)
    assert any("سعر 2" in lbl and "350" in lbl for lbl in labels)
    # variant id alone resolves the item (64-byte budget: no item id in the payload)
    rep = tap(db_session, cb_codec.build("var", menu["burger_v"][1].id))
    assert "برجر بلدي" in rep.text
    assert "." not in _flat(rep.button_rows)  # unit item: no decimal button
    tap(db_session, cb_codec.build("npd", 2))
    rep = tap(db_session, cb_codec.build("npc"))
    assert "700" in rep.text  # 2 x 350


def test_pagination_six_per_page(db_session, linked, menu):  # noqa: F811
    biz = linked["ids"]["a"]
    for i in range(8):
        _add_item(db_session, biz, "اللحوم", f"صنف رقم {i}",
                  [("عادي", Decimal("100.00"), True)])
    rep = tap(db_session, cb_codec.build("cat", "اللحوم", "p0"))
    item_buttons = [label for label, d in (row[0] for row in rep.button_rows)
                    if d.startswith("it:")]
    assert len(item_buttons) == 6
    assert "التالي ▶" in _flat(rep.button_rows) and "1/2" in _flat(rep.button_rows)
    rep = tap(db_session, cb_codec.build("cat", "اللحوم", "p1"))
    item_buttons = [label for label, d in (row[0] for row in rep.button_rows)
                    if d.startswith("it:")]
    assert len(item_buttons) == 3  # مفروم احمر + 8 added = 9 items, page 2 has 3
    assert "◀ السابق" in _flat(rep.button_rows)


def test_numpad_quantity_state_and_validation(db_session, linked, menu):  # noqa: F811
    tap(db_session, cb_codec.build("cat", "اللحوم", "p0"))
    tap(db_session, cb_codec.build("it", menu["meat"].id))
    # empty confirmation re-asks without losing the selection
    rep = tap(db_session, cb_codec.build("npc"))
    assert "اكتب الكمية الأول" in rep.text and "✅ تأكيد" in _flat(rep.button_rows)
    # a valid fraction lands in the cart at DB price: 2.5
    tap(db_session, cb_codec.build("npd", 2))
    tap(db_session, cb_codec.build("npdot"))
    tap(db_session, cb_codec.build("npd", 5))
    rep = tap(db_session, cb_codec.build("npc"))
    assert "2.5" in rep.text and "1125" in rep.text  # 2.5 x 450


def test_clear_cart_requires_confirmation(db_session, linked, menu):  # noqa: F811
    tap(db_session, cb_codec.build("cat", "اللحوم", "p0"))
    tap(db_session, cb_codec.build("it", menu["meat"].id))
    tap(db_session, cb_codec.build("npd", 1))
    tap(db_session, cb_codec.build("npc"))
    rep = tap(db_session, cb_codec.build("cclr"))
    assert "متأكد" in rep.text
    # "no" returns to the cart with the line intact
    rep = tap(db_session, cb_codec.build("cclr_no"))
    assert "مفروم احمر" in rep.text
    rep = tap(db_session, cb_codec.build("cclr"))
    rep = tap(db_session, cb_codec.build("cclr_yes"))
    assert "اتفضت السلة" in rep.text
    rep = tap(db_session, cb_codec.build("cart"))
    assert "فاضية" in rep.text


def test_forged_ids_get_stale_and_no_crash(db_session, linked, menu):  # noqa: F811
    rep = tap(db_session, cb_codec.build("it", uuid.uuid4()))
    assert "مش متاح" in rep.text
    rep = tap(db_session, cb_codec.build("var", uuid.uuid4()))
    assert "مش متاح" in rep.text
    # keypad taps without a selection in progress are stale, never an order line
    rep = tap(db_session, cb_codec.build("npd", 1))
    assert "مش متاح" in rep.text
    rep = tap(db_session, cb_codec.build("npc"))
    assert "مش متاح" in rep.text


def test_noop_acknowledges_without_message(db_session, linked, menu):  # noqa: F811
    rep = tap(db_session, cb_codec.build("noop"))
    assert rep.text == "" and rep.answer_callback_id


def test_codec_callbacks_on_legacy_business_do_nothing(db_session, linked):  # noqa: F811
    # linked but no menu rows: codec data is acknowledged and ignored
    rep = tap(db_session, cb_codec.build("cat", "اللحوم", "p0"))
    assert rep.text == ""


def test_callback_deduped_on_redelivery(db_session, linked, menu):  # noqa: F811
    upd = _cb(CHAT, cb_codec.build("cat", "اللحوم", "p0"))
    upd["update_id"] = 555001
    first = svc.handle_update(db_session, upd)
    second = svc.handle_update(db_session, upd)
    assert first and second == []


def test_prices_come_from_db_never_from_callback(db_session, linked, menu):  # noqa: F811
    # the codec carries ids and quantities only; this asserts the shape stays that way
    data = cb_codec.build("var", menu["burger_v"][0].id)
    assert "280" not in data and "برجر" not in data


# ---------- section 3.0: welcome + menu discovery ----------


def _photo_upd(chat_id, sizes):
    return {
        "update_id": next(_ids),
        "message": {
            "message_id": 21,
            "photo": [{"file_id": fid, "file_size": sz, "width": sz}
                      for fid, sz in sizes],
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False},
        },
    }


def test_start_shows_main_menu_directly(db_session, linked, menu):  # noqa: F811
    out = svc.handle_update(db_session, {
        "update_id": next(_ids),
        "message": {"message_id": 1, "text": "/start",
                    "chat": {"id": CHAT, "type": "private"},
                    "from": {"id": CHAT, "is_bot": False}}})
    rep = out[0]
    assert "أهلاً وسهلاً" in rep.text
    labels = _flat(rep.button_rows)
    # no onboarding gate (checkout spec 2026-11-26): categories + search + cart
    assert any("اللحوم" in lbl for lbl in labels)
    assert "🔍 بحث" in labels and "🛒 السلة" in labels
    assert "📋 عرض المنيو" not in labels
    assert "📍 العنوان" in labels and "📞 تواصل معنا" in labels
    assert rep.photo_file_id is None and "🖼 رفع صورة المنيو" not in labels  # customer


def test_start_is_text_and_meat_button_opens_brochure_photo(db_session, linked, menu):  # noqa: F811
    biz = db_session.scalar(select(Business))
    biz.menu_photo_file_id = "brochure-id"
    db_session.commit()
    out = svc.handle_update(db_session, {
        "update_id": next(_ids),
        "message": {"message_id": 1, "text": "/start",
                    "chat": {"id": CHAT, "type": "private"},
                    "from": {"id": CHAT, "is_bot": False}}})
    rep = out[0]
    assert rep.photo_file_id is None and "أهلاً وسهلاً" in rep.text  # /start = text only
    cats = tap(db_session, cb_codec.build("cats"))
    assert cats.photo_file_id == "brochure-id"
    assert any("اللحوم" in lbl for lbl in _flat(cats.button_rows))


def test_address_and_contact_buttons_are_separate(db_session, linked, menu):  # noqa: F811
    addr = tap(db_session, cb_codec.build("addr"))
    assert "منيا القمح" in addr.text and "01017597501" not in addr.text
    con = tap(db_session, cb_codec.build("contact"))
    assert "01017597501" in con.text and "01553799899" in con.text
    assert "منيا القمح" not in con.text


def test_owner_sees_upload_button_only_without_photo(db_session, linked, menu):  # noqa: F811
    _make_owner(db_session, CHAT)
    out = svc.handle_update(db_session, {
        "update_id": next(_ids),
        "message": {"message_id": 1, "text": "/start",
                    "chat": {"id": CHAT, "type": "private"},
                    "from": {"id": CHAT, "is_bot": False}}})
    assert "🖼 رفع صورة المنيو" in _flat(out[0].button_rows)


def _make_owner(db, chat_id):
    link = db.scalar(select(svc.TelegramLink).where(svc.TelegramLink.chat_id == chat_id))
    link.is_owner = True
    db.commit()


def test_show_menu_without_image_asks_for_upload(db_session, linked, menu):  # noqa: F811
    _make_owner(db_session, CHAT)  # owner links only since 19673006
    rep = tap(db_session, cb_codec.build("showmenu"))
    assert "صورة المنيو" in rep.text and rep.photo_file_id is None
    from app.chat import menu_buttons
    link = db_session.scalar(
        select(svc.TelegramLink).where(svc.TelegramLink.chat_id == CHAT))
    assert menu_buttons.wants_photo(db_session, link)


def test_photo_upload_stores_reference_and_acknowledges(db_session, linked, menu):  # noqa: F811
    _make_owner(db_session, CHAT)
    tap(db_session, cb_codec.build("showmenu"))
    out = svc.handle_update(db_session, _photo_upd(
        CHAT, [("small-id", 100), ("big-id", 900)]))
    assert "وصلتنا صورة المنيو" in out[0].text
    assert "🛒 ابدأ الطلب" in _flat(out[0].button_rows)
    from app.models import Business
    biz = db_session.get(Business, linked["ids"]["a"])
    assert biz.menu_photo_file_id == "big-id"  # largest size kept
    assert biz.menu_photo_uploaded_by == CHAT
    # now عرض المنيو sends the stored photo instead of asking
    rep = tap(db_session, cb_codec.build("showmenu"))
    assert rep.photo_file_id == "big-id" and rep.text == ""


def test_photo_without_waiting_state_ignored(db_session, linked, menu):  # noqa: F811
    out = svc.handle_update(db_session, _photo_upd(CHAT, [("x-id", 100)]))
    assert out == []
    from app.models import Business
    biz = db_session.get(Business, linked["ids"]["a"])
    assert biz.menu_photo_file_id is None


def test_contact_button_shows_shop_details(db_session, linked, menu):  # noqa: F811
    rep = tap(db_session, cb_codec.build("contact"))
    assert "تواصل معنا" in rep.text
    assert "01017597501" in rep.text and "01040756249" in rep.text
    assert "01553799899" in rep.text
    assert "🔙 رجوع" in _flat(rep.button_rows)


def test_welcome_has_meat_button_and_hides_pastry_until_it_exists(db_session, linked, menu):  # noqa: F811
    out = svc.handle_update(db_session, {
        "update_id": next(_ids),
        "message": {"message_id": 1, "text": "/start",
                    "chat": {"id": CHAT, "type": "private"},
                    "from": {"id": CHAT, "is_bot": False}}})
    labels = _flat(out[0].button_rows)
    assert "🥩 منيو اللحوم" in labels
    assert not any("الفطائر" in lbl for lbl in labels)
    assert not any(lbl.endswith("مصنعات وقطعيات") for lbl in labels)  # moved behind the button
    rep = tap(db_session, cb_codec.build("cats"))
    assert any("اللحوم" in lbl for lbl in _flat(rep.button_rows))


def test_back_from_address_and_contact_returns_to_welcome(db_session, linked, menu):  # noqa: F811
    for action in ("addr", "contact"):
        shown = tap(db_session, cb_codec.build(action))
        back = [d for lbl, d in (b for row in shown.button_rows for b in row)
                if "رجوع" in lbl][0]
        rep = tap(db_session, back)
        assert "أهلاً وسهلاً" in rep.text and rep.photo_file_id is None
        assert "🥩 منيو اللحوم" in _flat(rep.button_rows)


def test_welcome_greets_by_name_with_placeholder_fallback(db_session, linked, menu):  # noqa: F811
    from app.models_orders import Customer
    link = db_session.scalar(select(svc.TelegramLink).where(svc.TelegramLink.chat_id == CHAT))
    cust = db_session.get(Customer, link.customer_id)
    cust.name = "محمد"
    db_session.commit()
    rep = svc.handle_update(db_session, _start_upd())[0]
    assert rep.text.startswith("أهلاً وسهلاً بيك يا أستاذ / محمد")
    cust.name = f"عميل {CHAT}"
    db_session.commit()
    rep = svc.handle_update(db_session, _start_upd())[0]
    assert rep.text.startswith("أهلاً وسهلاً بيك ❤️") and "أستاذ" not in rep.text


def _start_upd():
    return {"update_id": next(_ids),
            "message": {"message_id": 1, "text": "/start",
                        "chat": {"id": CHAT, "type": "private"},
                        "from": {"id": CHAT, "is_bot": False}}}


def test_back_buttons_cats_and_pastry_return_to_welcome(db_session, linked, menu):  # noqa: F811
    biz = db_session.scalar(select(Business))
    db_session.add(MenuCategory(business_id=biz.id, name="الفطائر", sort_order=9))
    db_session.flush()
    item = MenuItem(business_id=biz.id, category="الفطائر", name="فطيرة تجربة",
                    sold_by="unit", sort_order=0)
    db_session.add(item)
    db_session.flush()
    db_session.add(MenuItemVariant(business_id=biz.id, item_id=item.id, name="M",
                                   price=Decimal("100"), is_default=True, sort_order=0))
    db_session.commit()
    home = cb_codec.build("home")
    cats = tap(db_session, cb_codec.build("cats"))
    assert home in _datas(cats.button_rows)
    assert not any("الفطائر" in lbl for lbl in _flat(cats.button_rows))  # own button
    past = tap(db_session, cb_codec.build("cat", "الفطائر", "p0"))
    assert home in _datas(past.button_rows)
    assert cb_codec.build("cats") not in _datas(past.button_rows)
    meat = tap(db_session, cb_codec.build("cat", "اللحوم", "p0"))
    assert cb_codec.build("cats") in _datas(meat.button_rows)  # meat items go back to meat menu
    rep = tap(db_session, home)
    assert "🥟 منيو الفطائر" in _flat(rep.button_rows)
