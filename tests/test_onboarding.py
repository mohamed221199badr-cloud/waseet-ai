"""Feature 2 (19758722): mandatory onboarding - phone via native contact share,
delivery vs pickup, address for delivery. CRM persistence, bypass prevention."""

import pytest
from sqlalchemy import select

from app.chat import callbacks as cb_codec
from app.config import settings
from app.models_orders import Customer
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


@pytest.fixture()
def linked_new(db_session, linked):  # noqa: F811
    """The linked menu customer with an EMPTY profile, as a brand-new customer."""
    cust = db_session.scalar(
        select(Customer).where(Customer.business_id == linked["ids"]["a"]))
    cust.phone, cust.address, cust.delivery_method = None, None, None
    db_session.commit()
    return linked


def _customer(db, linked):  # noqa: F811
    return db.scalar(select(Customer).where(Customer.business_id == linked["ids"]["a"]))


def test_new_customer_start_asks_phone(db_session, linked_new, menu):  # noqa: F811
    out = _say(db_session, "/start")
    rep = out[0]
    assert "رقم تليفونك" in rep.text
    assert rep.reply_keyboard and rep.reply_keyboard[0][0]["request_contact"] is True


def test_returning_complete_profile_goes_straight_to_menu(db_session, linked, menu):  # noqa: F811
    cust = _customer(db_session, linked)
    cust.phone, cust.delivery_method, cust.address = "01000000001", "pickup", None
    db_session.commit()
    out = _say(db_session, "/start")
    assert "أهلاً وسهلاً" in out[0].text and out[0].reply_keyboard is None


def test_returning_missing_delivery_asks_only_that(db_session, linked, menu):  # noqa: F811
    cust = _customer(db_session, linked)
    cust.phone, cust.delivery_method = "01000000001", None
    db_session.commit()
    out = _say(db_session, "/start")
    assert "يوصلك لحد عندك" in out[0].text
    assert out[0].remove_keyboard is True  # any stale reply keyboard is dropped


def test_contact_share_delivery_address_and_crm(db_session, linked_new, menu):  # noqa: F811
    _say(db_session, "/start")
    out = _contact(db_session, phone="٠١٠١٧٥٩٧٥٠١")  # Arabic digits normalize
    assert "يوصلك لحد عندك" in out[0].text and out[0].remove_keyboard is True
    rep = tap(db_session, cb_codec.build("dv", "delivery"))
    assert "عنوانك بالتفصيل" in rep.text
    out = _say(db_session, "مدينة نصر، شارع عباس العقاد، عمارة 12 شقة 3")
    assert "أهلاً وسهلاً" in out[0].text and out[0].remove_keyboard is True
    cust = _customer(db_session, linked_new)
    assert cust.phone == "01017597501" and cust.delivery_method == "delivery"
    assert "عباس العقاد" in cust.address
    # profile complete now: menu callbacks execute
    rep = tap(db_session, cb_codec.build("cats"))
    assert any("اللحوم" in lbl for lbl in _flat(rep.button_rows))


def test_pickup_needs_no_address(db_session, linked_new, menu):  # noqa: F811
    _say(db_session, "/start")
    _contact(db_session)
    rep = tap(db_session, cb_codec.build("dv", "pickup"))
    assert "أهلاً وسهلاً" in rep.text
    assert _customer(db_session, linked_new).delivery_method == "pickup"


def test_contact_ownership_and_bad_phone_rejected(db_session, linked_new, menu):  # noqa: F811
    _say(db_session, "/start")
    out = _contact(db_session, user_id=999999)  # someone else's contact card
    assert "رقمك أنت" in out[0].text and out[0].reply_keyboard is not None
    out = _contact(db_session, phone="123")  # too short
    assert "مش صح" in out[0].text
    assert _customer(db_session, linked_new).phone is None


def test_empty_and_short_address_rejected(db_session, linked, menu):  # noqa: F811
    cust = _customer(db_session, linked)
    cust.phone, cust.delivery_method = "01000000001", "delivery"
    db_session.commit()
    _say(db_session, "/start")  # opens the address step
    out = _say(db_session, "مد")
    assert "قصير" in out[0].text
    out = _say(db_session, "فيصل، شارع الملك فيصل، عمارة 7")
    assert "أهلاً وسهلاً" in out[0].text


def test_bypass_prevention_and_repeated_start(db_session, linked_new, menu):  # noqa: F811
    _say(db_session, "/start")
    # menu taps while the profile is incomplete never execute the flow
    rep = tap(db_session, cb_codec.build("cats"))
    assert "رقم تليفونك" in rep.text
    rep = tap(db_session, cb_codec.build("it", menu["meat"].id))
    assert "رقم تليفونك" in rep.text
    # typed orders are gated too: no order line created
    out = _say(db_session, "كيلو مفروم")
    assert "رقم تليفونك" in out[0].text
    from app.models_orders import Order
    assert db_session.scalar(select(Order)) is None
    # repeated /start resumes the same step without breaking anything
    out = _say(db_session, "/start")
    assert "رقم تليفونك" in out[0].text
    out = _say(db_session, "/start")
    assert "رقم تليفونك" in out[0].text


def test_interrupted_onboarding_recovers(db_session, linked_new, menu):  # noqa: F811
    # phone collected, then the customer disappears mid-flow and comes back:
    # CRM data decides the step, no stale session flag can skip or repeat it
    _say(db_session, "/start")
    _contact(db_session)
    cust = _customer(db_session, linked_new)
    assert cust.phone == "+201017597501"
    out = _say(db_session, "أهلا")  # plain text mid-onboarding
    assert "يوصلك لحد عندك" in out[0].text  # resumes at the missing field


def test_typed_delivery_answers_work_without_buttons(db_session, linked_new, menu):  # noqa: F811
    # hotfix 19793273: he typed the answer when the buttons were missing
    _say(db_session, "/start")
    _contact(db_session)
    out = _say(db_session, "من المحل")
    assert "أهلاً وسهلاً" in out[0].text
    assert _customer(db_session, linked_new).delivery_method == "pickup"


def test_typed_delivery_then_address(db_session, linked_new, menu):  # noqa: F811
    _say(db_session, "/start")
    _contact(db_session)
    out = _say(db_session, "توصيل")
    assert "عنوانك بالتفصيل" in out[0].text
    out = _say(db_session, "المعادي، شارع 9، عمارة 3")
    assert "أهلاً وسهلاً" in out[0].text
    assert _customer(db_session, linked_new).delivery_method == "delivery"


def test_unrelated_text_still_reshows_step(db_session, linked_new, menu):  # noqa: F811
    _say(db_session, "/start")
    _contact(db_session)
    out = _say(db_session, "اهلا")
    assert "يوصلك لحد عندك" in out[0].text
    assert _customer(db_session, linked_new).delivery_method is None
