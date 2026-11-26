"""Two-way cashier confirmation: ✅/❌ on the notice, forward to kitchen + accounts."""

import pytest
from sqlalchemy import select

from app.chat import callbacks as cb_codec
from app.config import settings
from app.models_orders import Customer, Order, TelegramLink
from app.telegram import service as svc
from tests.test_checkout import _fill_cart, _say  # noqa: F401
from tests.test_intake import shop  # noqa: F401
from tests.test_menu_buttons_v18 import (  # noqa: F401, F811
    CHAT,
    _cb,
    _datas,
    linked,
    menu,
    tap,
)
from tests.test_migrations import _alembic, scratch_url  # noqa: F401

OWNER = 777001


@pytest.fixture(autouse=True)
def _agent_on(monkeypatch):
    monkeypatch.setattr(settings, "order_auto_confirm", False)  # these test the tap flow
    monkeypatch.setattr(settings, "chat_agent_enabled", True)
    monkeypatch.setattr(settings, "llm_mode", "off")


def _owner(db, biz):
    staff = Customer(business_id=biz, name="كاشير", telegram_chat_id=OWNER)
    db.add(staff)
    db.flush()
    db.add(TelegramLink(business_id=biz, customer_id=staff.id, chat_id=OWNER, is_owner=True))
    db.commit()


def _place(db, menu):  # noqa: F811
    _fill_cart(db, menu)
    tap(db, cb_codec.build("ckgo"))
    tap(db, cb_codec.build("ckdel", "pickup"))
    out = svc.handle_update(db, _cb(CHAT, cb_codec.build("ckok")))
    notice = next(r for r in out if r.chat_id == OWNER)
    return db.scalar(select(Order)), notice


def _tap(db, chat, data, uid=[990000]):  # noqa: B006
    uid[0] += 1
    return svc.handle_update(db, {
        "update_id": uid[0],
        "callback_query": {"id": f"cq{uid[0]}", "data": data,
                           "from": {"id": chat, "is_bot": False, "first_name": "سامي"},
                           "message": {"message_id": 5, "text": "🔔 طلب جديد رقم x",
                                       "chat": {"id": chat, "type": "private"}}}})


def test_notice_has_confirm_and_reject_buttons(db_session, linked, menu):  # noqa: F811
    _owner(db_session, linked["ids"]["a"])
    order, notice = _place(db_session, menu)
    assert cb_codec.build("oky", order.id) in _datas(notice.button_rows)
    assert cb_codec.build("orj", order.id) in _datas(notice.button_rows)


def test_confirm_updates_status_notifies_customer_kitchen_accounts(db_session, linked, menu):  # noqa: F811
    _owner(db_session, linked["ids"]["a"])
    order, _ = _place(db_session, menu)
    out = _tap(db_session, OWNER, cb_codec.build("oky", order.id))
    db_session.refresh(order)
    assert order.status == "confirmed" and order.decided_at is not None
    texts = [(r.chat_id, r.text) for r in out]
    assert any(c == CHAT and "اتأكد" in t for c, t in texts)
    kitchen = [t for c, t in texts if c == OWNER and t.startswith("🍳 مطبخ")]
    accounts = [t for c, t in texts if c == OWNER and t.startswith("💰 حسابات")]
    assert len(kitchen) == 1 and len(accounts) == 1
    assert "مفروم احمر" in kitchen[0] and "×" in kitchen[0] and "=" not in kitchen[0]
    assert "الإجمالي" in accounts[0] and "×" in accounts[0]
    edited = out[0]
    assert edited.edit_message_id == 5 and edited.button_rows == []
    assert "🔔 طلب جديد رقم x" in edited.text and "اتأكد" in edited.text


def test_double_tap_changes_nothing_and_notifies_nobody(db_session, linked, menu):  # noqa: F811
    _owner(db_session, linked["ids"]["a"])
    order, _ = _place(db_session, menu)
    _tap(db_session, OWNER, cb_codec.build("oky", order.id))
    again = _tap(db_session, OWNER, cb_codec.build("oky", order.id))
    assert len(again) == 1 and "اتقرر قبل كده" in again[0].text
    assert again[0].edit_message_id is None
    reject = _tap(db_session, OWNER, cb_codec.build("orr", order.id, "no"))
    assert len(reject) == 1 and "اتقرر قبل كده" in reject[0].text
    db_session.refresh(order)
    assert order.status == "confirmed"


def test_reject_asks_reason_then_rejects_and_tells_customer(db_session, linked, menu):  # noqa: F811
    _owner(db_session, linked["ids"]["a"])
    order, _ = _place(db_session, menu)
    ask = _tap(db_session, OWNER, cb_codec.build("orj", order.id))
    assert cb_codec.build("orr", order.id, "na") in _datas(ask[0].button_rows)
    db_session.refresh(order)
    assert order.status == "pending"
    out = _tap(db_session, OWNER, cb_codec.build("orr", order.id, "na"))
    db_session.refresh(order)
    assert order.status == "rejected" and order.rejection_reason == "الصنف مش متاح حالياً"
    assert any(r.chat_id == CHAT and "اتلغى" in r.text and "السبب" in r.text for r in out)
    assert not any(r.text.startswith(("🍳", "💰")) for r in out)


def test_only_owner_or_cashier_chats_may_tap(db_session, linked, menu):  # noqa: F811
    _owner(db_session, linked["ids"]["a"])
    order, _ = _place(db_session, menu)
    out = _tap(db_session, CHAT, cb_codec.build("oky", order.id))  # the customer
    assert "مش مسموح" in out[0].text
    stranger = _tap(db_session, 424242, cb_codec.build("oky", order.id))
    assert "مش مسموح" in stranger[0].text
    db_session.refresh(order)
    assert order.status == "pending"


def test_env_cashier_chat_without_link_can_confirm_and_env_lists_route(  # noqa: F811
        db_session, linked, menu, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "cashier_telegram_chat_ids", "888001")
    monkeypatch.setattr(settings, "kitchen_telegram_chat_ids", "888002")
    monkeypatch.setattr(settings, "accounts_telegram_chat_ids", "888003")
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("ckgo"))
    tap(db_session, cb_codec.build("ckdel", "pickup"))
    svc.handle_update(db_session, _cb(CHAT, cb_codec.build("ckok")))
    order = db_session.scalar(select(Order))
    out = _tap(db_session, 888001, cb_codec.build("oky", order.id))
    by_chat = {r.chat_id: r.text for r in out}
    assert by_chat[888002].startswith("🍳 مطبخ") and by_chat[888003].startswith("💰 حسابات")
    db_session.refresh(order)
    assert order.status == "confirmed"


def test_callback_codec_rejects_bad_payloads():
    assert cb_codec.parse("oky:not-a-uuid") is None
    assert cb_codec.parse("orr:00000000-0000-0000-0000-000000000000:zzz") is None
    assert cb_codec.parse("oky:00000000-0000-0000-0000-000000000000") is not None


def test_reason_screen_has_back_to_the_notice_buttons(db_session, linked, menu):  # noqa: F811
    _owner(db_session, linked["ids"]["a"])
    order, _ = _place(db_session, menu)
    ask = _tap(db_session, OWNER, cb_codec.build("orj", order.id))
    assert cb_codec.build("orb", order.id) in _datas(ask[0].button_rows)
    back = _tap(db_session, OWNER, cb_codec.build("orb", order.id))
    assert back[0].text == "🔔 طلب جديد رقم x"
    assert cb_codec.build("oky", order.id) in _datas(back[0].button_rows)
    db_session.refresh(order)
    assert order.status == "pending"


def _age(db, order, minutes):
    from sqlalchemy import text
    db.execute(text(f"UPDATE orders SET created_at = now() - interval '{minutes} minutes' "
                    "WHERE id = :i"), {"i": order.id})
    db.commit()


def test_pending_order_reminders_at_3_and_10_minutes_once_each(db_session, linked, menu):  # noqa: F811
    from app.chat import reminders
    _owner(db_session, linked["ids"]["a"])
    order, _ = _place(db_session, menu)
    assert reminders.due(db_session) == []                  # fresh: nothing yet
    _age(db_session, order, 4)
    first = reminders.due(db_session)
    assert [c for c, _, _ in first] == [OWNER] and "لسه مستني" in first[0][1]
    assert cb_codec.build("oky", order.id) in _datas(first[0][2])
    assert reminders.due(db_session) == []                  # not repeated
    _age(db_session, order, 11)
    second = reminders.due(db_session)
    assert len(second) == 1 and "10 دقايق" in second[0][1]
    assert reminders.due(db_session) == []                  # nothing after the 2nd


def test_no_reminder_once_decided_or_for_old_orders(db_session, linked, menu):  # noqa: F811
    from app.chat import reminders
    _owner(db_session, linked["ids"]["a"])
    order, _ = _place(db_session, menu)
    _tap(db_session, OWNER, cb_codec.build("oky", order.id))
    _age(db_session, order, 11)
    assert reminders.due(db_session) == []                  # confirmed
    order.status = "pending"
    db_session.commit()
    _age(db_session, order, 45)
    assert reminders.due(db_session) == []                  # older than the 30 min window


def test_auto_confirm_sends_everything_at_once(db_session, linked, menu, monkeypatch):  # noqa: F811
    monkeypatch.setattr(settings, "order_auto_confirm", True)
    _owner(db_session, linked["ids"]["a"])
    _fill_cart(db_session, menu)
    tap(db_session, cb_codec.build("ckgo"))
    tap(db_session, cb_codec.build("ckdel", "pickup"))
    out = svc.handle_update(db_session, _cb(CHAT, cb_codec.build("ckok")))
    order = db_session.scalar(select(Order))
    assert order.status == "confirmed" and order.decided_at is not None
    assert "طلبك اتأكد" in out[0].text
    owner_texts = [r.text for r in out if r.chat_id == OWNER]
    assert len(owner_texts) == 3
    notice = next(r for r in out if r.chat_id == OWNER and r.text.startswith("🔔"))
    assert not notice.button_rows and "متأكد تلقائياً" in notice.text
    assert any(t.startswith("🍳 مطبخ") for t in owner_texts)
    assert any(t.startswith("💰 حسابات") for t in owner_texts)
    from app.chat import reminders
    _age(db_session, order, 5)
    assert reminders.due(db_session) == []       # confirmed: no reminder
