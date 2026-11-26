"""Per-category page photos (m014): an owner uploads a photo captioned with the
category name; customers tapping that category get one message - the photo with
the items header as its caption and the item buttons under it.

Covers: upload save, unknown captions, non-owner photos staying ignored, the
prompted menu-image flow keeping priority, the customer tap returning photo +
rows, and the client sending the photo with the caption in one sendPhoto call."""

import itertools
import json

import httpx
import pytest
from sqlalchemy import select

from app.chat import callbacks as cb_codec
from app.chat import menu_buttons
from app.config import settings
from app.models import AuditEvent
from app.models_menu import MenuCategory
from app.telegram import service as svc
from tests.test_intake import shop  # noqa: F401
from tests.test_menu_buttons_v18 import CHAT, _flat, linked, menu, tap  # noqa: F401
from tests.test_telegram import mock_client

_ids = itertools.count(880000)


@pytest.fixture(autouse=True)
def _agent_on(monkeypatch):
    monkeypatch.setattr(settings, "chat_agent_enabled", True)
    monkeypatch.setattr(settings, "llm_mode", "off")


def _make_owner(db, chat_id=CHAT):
    link = db.scalar(select(svc.TelegramLink).where(svc.TelegramLink.chat_id == chat_id))
    link.is_owner = True
    db.commit()
    return link


def _photo_upd(chat_id, file_id, caption=None):
    msg = {"message_id": 31,
           "photo": [{"file_id": file_id, "file_size": 500, "width": 500}],
           "chat": {"id": chat_id, "type": "private"},
           "from": {"id": chat_id, "is_bot": False}}
    if caption is not None:
        msg["caption"] = caption
    return {"update_id": next(_ids), "message": msg}


def test_owner_caption_photo_sets_category_photo(db_session, linked, menu):  # noqa: F811
    _make_owner(db_session)
    out = svc.handle_update(db_session, _photo_upd(CHAT, "file-meat", caption="اللحوم"))
    assert out and out[0].text == menu_buttons.MSG_CATEGORY_PHOTO_SAVED.format(category="اللحوم")
    cat = db_session.scalar(select(MenuCategory).where(
        MenuCategory.business_id == linked["ids"]["a"], MenuCategory.name == "اللحوم"))
    assert cat.photo_file_id == "file-meat"
    audit = db_session.scalars(select(AuditEvent).where(
        AuditEvent.action == "photo_set").order_by(AuditEvent.id.desc())).first()
    assert audit is not None and audit.payload["category"] == "اللحوم"
    assert audit.entity == "menu_category" and audit.entity_id == str(cat.id)


def test_caption_upload_replaces_previous_photo(db_session, linked, menu):  # noqa: F811
    _make_owner(db_session)
    svc.handle_update(db_session, _photo_upd(CHAT, "old-file", caption="اللحوم"))
    svc.handle_update(db_session, _photo_upd(CHAT, "new-file", caption="  اللحوم  "))  # trims
    cat = db_session.scalar(select(MenuCategory).where(
        MenuCategory.business_id == linked["ids"]["a"], MenuCategory.name == "اللحوم"))
    assert cat.photo_file_id == "new-file"


def test_unknown_caption_lists_categories(db_session, linked, menu):  # noqa: F811
    _make_owner(db_session)
    out = svc.handle_update(db_session, _photo_upd(CHAT, "f1", caption="مش موجود"))
    want = menu_buttons.MSG_CATEGORY_PHOTO_NONE.format(categories="- اللحوم\n- مصنعات")
    assert out and out[0].text == want
    cat = db_session.scalar(select(MenuCategory).where(
        MenuCategory.business_id == linked["ids"]["a"], MenuCategory.name == "اللحوم"))
    assert cat.photo_file_id is None


def test_non_owner_caption_photo_is_ignored(db_session, linked, menu):  # noqa: F811
    # customers send photos all the time (a dish, a question): silently ignored,
    # never an upload attempt (spec 3.0B)
    out = svc.handle_update(db_session, _photo_upd(CHAT, "f1", caption="اللحوم"))
    assert out == []
    cat = db_session.scalar(select(MenuCategory).where(
        MenuCategory.business_id == linked["ids"]["a"], MenuCategory.name == "اللحوم"))
    assert cat.photo_file_id is None


def test_menu_image_prompt_keeps_priority_over_caption(db_session, linked, menu):  # noqa: F811
    # while عرض المنيو is waiting for the menu photo, even a captioned photo goes
    # to that flow (it is the one the owner was explicitly asked for)
    link = _make_owner(db_session)
    tap(db_session, cb_codec.build("showmenu"))
    assert menu_buttons.wants_photo(db_session, link)
    svc.handle_update(db_session, _photo_upd(CHAT, "menu-file", caption="اللحوم"))
    from app.models import Business
    biz = db_session.get(Business, linked["ids"]["a"])
    assert biz.menu_photo_file_id == "menu-file"
    cat = db_session.scalar(select(MenuCategory).where(
        MenuCategory.business_id == linked["ids"]["a"], MenuCategory.name == "اللحوم"))
    assert cat.photo_file_id is None


def test_category_tap_returns_photo_with_rows(db_session, linked, menu):  # noqa: F811
    _make_owner(db_session)
    svc.handle_update(db_session, _photo_upd(CHAT, "file-meat", caption="اللحوم"))
    rep = tap(db_session, cb_codec.build("cat", "اللحوم", "p0"))
    assert rep.photo_file_id == "file-meat"
    assert rep.text == "أصناف «اللحوم»:"
    labels = _flat(rep.button_rows)
    assert any("مفروم احمر" in lbl for lbl in labels)


def test_category_without_photo_stays_text(db_session, linked, menu):  # noqa: F811
    rep = tap(db_session, cb_codec.build("cat", "مصنعات", "p0"))
    assert rep.photo_file_id is None
    assert rep.text == "أصناف «مصنعات»:"


def test_stale_category_with_photo_stays_stale(db_session, linked, menu):  # noqa: F811
    _make_owner(db_session)
    svc.handle_update(db_session, _photo_upd(CHAT, "file-meat", caption="اللحوم"))
    rep = tap(db_session, cb_codec.build("cat", "قسم اتمسح", "p0"))
    assert rep.photo_file_id is None
    assert rep.text == menu_buttons.MSG_STALE


def test_client_send_photo_carries_caption_and_buttons():
    seen = {}

    def capture(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 9}})

    c = mock_client(capture)
    mid = c.send_photo(1, "file-meat", [[("🥩 مفروم", "it:x")]], caption="أصناف «اللحوم»:")
    assert mid == 9
    assert seen["photo"] == "file-meat"
    assert seen["caption"] == "أصناف «اللحوم»:"
    assert seen["reply_markup"]["inline_keyboard"][0][0]["text"] == "🥩 مفروم"
