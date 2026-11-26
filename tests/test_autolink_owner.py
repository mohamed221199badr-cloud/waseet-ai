"""UI v1.8: auto-link of unlinked chats to the single menu business (19672361)
and owner-only official menu photo uploads (19673006).

Auto-link: an unlinked private chat gets a customer + link on the unique menu
business and enters through the welcome screen; /stop then a message re-links to
the SAME customer (customers.telegram_chat_id); deactivated customers are never
re-linked; with zero or several menu businesses the old not-linked message stays.
Owner: telegram_links.is_owner is set only through the owner-authenticated API;
a non-owner photo never becomes the official menu image."""

import itertools

import pytest
from sqlalchemy import select

from app.auth import create_access_token
from app.chat import callbacks as cb_codec
from app.chat import menu_buttons
from app.config import settings
from app.models import User
from app.models_menu import MenuCategory, MenuItem, MenuItemVariant
from app.models_orders import Customer, TelegramLink
from app.telegram import service as svc
from tests.test_intake import shop  # noqa: F401
from tests.test_migrations import _alembic, scratch_url  # noqa: F401

_ids = itertools.count(660000)
CHAT = 601


@pytest.fixture(autouse=True)
def _agent_on(monkeypatch):
    monkeypatch.setattr(settings, "chat_agent_enabled", True)
    monkeypatch.setattr(settings, "llm_mode", "off")


def _give_menu(db, biz):
    db.add(MenuCategory(business_id=biz, name="اللحوم"))
    item = MenuItem(business_id=biz, category="اللحوم", name="مفروم",
                    sold_by="weight", unit_label="كيلو")
    db.add(item)
    db.flush()
    db.add(MenuItemVariant(business_id=biz, item_id=item.id, name="سعر 1",
                           price=100, is_default=True))
    db.commit()


@pytest.fixture()
def menu(db_session, shop):  # noqa: F811
    _give_menu(db_session, shop["ids"]["a"])
    return shop


def _text(chat_id, body, first_name="زبون", last_name=None):
    frm = {"id": chat_id, "is_bot": False, "first_name": first_name}
    if last_name:
        frm["last_name"] = last_name
    return {
        "update_id": next(_ids),
        "message": {"message_id": 1, "text": body,
                    "chat": {"id": chat_id, "type": "private"},
                    "from": frm},
    }


def _cb(chat_id, data):
    return {
        "update_id": next(_ids),
        "callback_query": {
            "id": f"cbq-{next(_ids)}", "data": data,
            "from": {"id": chat_id, "is_bot": False},
            "message": {"message_id": 7,
                        "chat": {"id": chat_id, "type": "private"}},
        },
    }


def _photo(chat_id, file_id="photo-1"):
    return {
        "update_id": next(_ids),
        "message": {"message_id": 3,
                    "photo": [{"file_id": file_id, "file_size": 100, "width": 100}],
                    "chat": {"id": chat_id, "type": "private"},
                    "from": {"id": chat_id, "is_bot": False}},
    }


def _link(db, chat_id):
    return db.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))


def test_first_message_auto_links_and_welcomes(db_session, menu):  # noqa: F811
    out = svc.handle_update(db_session, _text(CHAT, "اهلا"))
    # no onboarding gate (checkout spec 2026-11-26): the main menu directly
    assert "أهلاً وسهلاً" in out[0].text
    labels = [lbl for row in out[0].button_rows for lbl, _ in row]
    assert "🔍 بحث" not in labels and "🛒 السلة" in labels
    link = _link(db_session, CHAT)
    assert link is not None and link.business_id == menu["ids"]["a"]
    cust = db_session.get(Customer, link.customer_id)
    assert cust.telegram_chat_id == CHAT and cust.name == "زبون"
    out = svc.handle_update(db_session, _text(CHAT, "/start"))
    assert "أهلاً وسهلاً" in out[0].text
    assert "📋 عرض المنيو" not in [lbl for row in out[0].button_rows for lbl, _ in row]


def test_repeat_contact_reuses_the_same_customer(db_session, menu):  # noqa: F811
    svc.handle_update(db_session, _text(CHAT, "اهلا"))
    svc.handle_update(db_session, _text(CHAT, "الأصناف"))
    links = db_session.scalars(select(TelegramLink)).all()
    assert len(links) == 1
    assert db_session.scalar(select(Customer).where(
        Customer.telegram_chat_id == CHAT).limit(1)) is not None
    assert len(db_session.scalars(select(Customer)).all()) == 2  # auto + shop fixture's


def test_stop_then_message_relinks_same_customer(db_session, menu):  # noqa: F811
    svc.handle_update(db_session, _text(CHAT, "اهلا"))
    cust_id = _link(db_session, CHAT).customer_id
    out = svc.handle_update(db_session, _text(CHAT, "/stop"))
    assert out[0].text == svc.MSG_UNLINKED
    assert _link(db_session, CHAT) is None
    svc.handle_update(db_session, _text(CHAT, "اهلا تاني"))
    link = _link(db_session, CHAT)
    assert link is not None and link.customer_id == cust_id  # history survives


def test_deactivated_customer_is_never_relinked(db_session, menu):  # noqa: F811
    svc.handle_update(db_session, _text(CHAT, "اهلا"))
    cust = db_session.get(Customer, _link(db_session, CHAT).customer_id)
    svc.handle_update(db_session, _text(CHAT, "/stop"))
    cust.is_active = False
    db_session.commit()
    out = svc.handle_update(db_session, _text(CHAT, "اهلا"))
    assert out[0].text == svc.MSG_NOT_LINKED
    assert _link(db_session, CHAT) is None


def test_two_menu_businesses_keep_the_old_message(db_session, menu):  # noqa: F811
    _give_menu(db_session, menu["ids"]["b"])
    out = svc.handle_update(db_session, _text(CHAT, "اهلا"))
    assert out[0].text == svc.MSG_NOT_LINKED
    assert _link(db_session, CHAT) is None


def test_start_with_old_code_is_just_the_welcome(db_session, client, menu):  # noqa: F811
    svc.handle_update(db_session, _text(CHAT, "اهلا"))
    auto_cust = _link(db_session, CHAT).customer_id
    out = svc.handle_update(db_session, _text(CHAT, "/start TH5LTF4SWE"))
    assert "تم ربط الحساب" not in out[0].text and "أهلاً وسهلاً" in out[0].text
    assert _link(db_session, CHAT).customer_id == auto_cust  # nothing re-pointed


def test_unlinked_callback_auto_links_with_welcome(db_session, menu):  # noqa: F811
    out = svc.handle_update(db_session, _cb(CHAT, cb_codec.build("cats")))
    # auto-linked on the tap, straight to the main menu (no onboarding gate)
    assert "أهلاً وسهلاً" in out[0].text
    assert _link(db_session, CHAT) is not None


def _complete_profile(db, chat_id):
    """Fill the CRM profile fields directly (checkout collects them at order time)."""
    link = db.scalar(select(TelegramLink).where(TelegramLink.chat_id == chat_id))
    cust = db.get(Customer, link.customer_id)
    cust.phone, cust.delivery_method = "01022222222", "pickup"
    db.commit()


# ---------- owner-only menu photo ----------

def test_non_owner_showmenu_without_photo_gets_soon(db_session, menu):  # noqa: F811
    svc.handle_update(db_session, _text(CHAT, "اهلا"))
    _complete_profile(db_session, CHAT)
    out = svc.handle_update(db_session, _cb(CHAT, cb_codec.build("showmenu")))
    assert "قريب" in out[0].text
    assert not menu_buttons.wants_photo(db_session, _link(db_session, CHAT))


def test_owner_uploads_official_photo(db_session, menu):  # noqa: F811
    svc.handle_update(db_session, _text(CHAT, "اهلا"))
    link = _link(db_session, CHAT)
    link.is_owner = True
    _complete_profile(db_session, CHAT)
    out = svc.handle_update(db_session, _cb(CHAT, cb_codec.build("showmenu")))
    assert "صورة المنيو" in out[0].text
    out = svc.handle_update(db_session, _photo(CHAT))
    assert "وصلتنا صورة المنيو" in out[0].text
    from app.models import Business
    biz = db_session.get(Business, menu["ids"]["a"])
    assert biz.menu_photo_file_id == "photo-1"
    assert biz.menu_photo_uploaded_by == CHAT


def test_non_owner_photo_never_becomes_menu(db_session, menu):  # noqa: F811
    svc.handle_update(db_session, _text(CHAT, "اهلا"))
    link = _link(db_session, CHAT)
    # defense in depth: even with a forged waiting state, a non-owner store refuses
    text, _rows = menu_buttons.handle_photo(db_session, link, "forged-id")
    assert "صاحب المحل" in text
    from app.models import Business
    biz = db_session.get(Business, menu["ids"]["a"])
    assert biz.menu_photo_file_id is None


def test_mark_owner_endpoint_owner_only(db_session, client, menu):  # noqa: F811
    svc.handle_update(db_session, _text(CHAT, "اهلا"))
    link = _link(db_session, CHAT)
    r = client.post(f"/api/v1/telegram/links/{link.id}/owner", headers=menu["h"])
    assert r.status_code == 204
    db_session.expire_all()
    assert _link(db_session, CHAT).is_owner is True
    # a cashier token gets 403
    cashier = User(business_id=menu["ids"]["a"], full_name="كاشير",
                   email="cashier-x@test.local", password_hash="x", role="cashier")
    db_session.add(cashier)
    db_session.commit()
    token, _ = create_access_token(cashier.id, menu["ids"]["a"])
    r = client.post(f"/api/v1/telegram/links/{link.id}/owner",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_greeting_uses_full_name_first_and_last(db_session, menu):  # noqa: F811
    rep = svc.handle_update(db_session, _text(910001, "/start", "محمد", "بدر"))[0]
    assert rep.text.startswith("أهلاً وسهلاً بيك يا أستاذ / محمد بدر ❤️")


def test_greeting_first_name_only_when_no_last_name(db_session, menu):  # noqa: F811
    rep = svc.handle_update(db_session, _text(910002, "/start", "محمد"))[0]
    assert rep.text.startswith("أهلاً وسهلاً بيك يا أستاذ / محمد ❤️")
    assert "محمد  " not in rep.text


def test_existing_first_name_customer_is_upgraded_to_full_name(db_session, menu):  # noqa: F811
    svc.handle_update(db_session, _text(910003, "/start", "منى"))          # saved as "منى"
    rep = svc.handle_update(db_session, _text(910003, "/start", "منى", "السيد"))[0]
    assert rep.text.startswith("أهلاً وسهلاً بيك يا أستاذ / منى السيد ❤️")


def test_owner_edited_name_is_not_overwritten(db_session, menu):  # noqa: F811
    from app.models_orders import Customer
    svc.handle_update(db_session, _text(910004, "/start", "منى"))
    cust = db_session.scalar(select(Customer).where(Customer.telegram_chat_id == 910004))
    cust.name = "الحاجة منى"
    db_session.commit()
    rep = svc.handle_update(db_session, _text(910004, "/start", "منى", "السيد"))[0]
    assert "الحاجة منى" in rep.text and "منى السيد" not in rep.text
