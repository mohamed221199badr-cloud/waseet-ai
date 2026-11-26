"""Slice A: Telegram inline buttons over the rules chat agent (no LLM, no network).

Covers the callback whitelist, candidate pick buttons, cart confirm/cancel, view_cart,
category listing, stale/forged/foreign callbacks, and callback dedupe."""

import itertools
import uuid

import pytest
from sqlalchemy import select

from app.config import settings
from app.models import Product
from app.models_orders import Order
from app.telegram import service as svc
from tests.test_intake import shop  # noqa: F401
from tests.test_migrations import _alembic, scratch_url  # noqa: F401
from tests.test_telegram import issue, say, upd

_ids = itertools.count(90000)


@pytest.fixture(autouse=True)
def _agent_on(monkeypatch):
    monkeypatch.setattr(settings, "chat_agent_enabled", True)
    monkeypatch.setattr(settings, "llm_mode", "off")


@pytest.fixture()
def linked(client, db_session, shop):  # noqa: F811
    say(db_session, 500, f"/start {issue(client, shop)}")
    return shop


def cb(chat_id, data, update_id=None, chat_type="private", is_bot=False):
    return {
        "update_id": update_id if update_id is not None else next(_ids),
        "callback_query": {
            "id": f"cbq-{next(_ids)}", "data": data,
            "from": {"id": chat_id, "is_bot": is_bot},
            "message": {"chat": {"id": chat_id, "type": chat_type}},
        },
    }


def tap(db, chat_id, data, **kw):
    return svc.handle_update(db, cb(chat_id, data, **kw))


def _pid(db, name):
    return str(db.scalar(select(Product).where(Product.name == name)).id)


def test_greeting_gets_menu_buttons(db_session, linked):  # noqa: F811
    out = svc.handle_update(db_session, upd(500, "اهلا"))
    assert out and out[0].buttons
    callbacks = {cb_ for _, cb_ in out[0].buttons}
    assert {"list_products", "view_cart"} <= callbacks
    # the demo shop's SKUs (SU/SA/TE) match no trial category prefix -> no cat_ buttons
    assert not any(c.startswith("cat_") for c in callbacks)


def _add_product(db, shop_, name, sku, price):
    p = Product(business_id=shop_["ids"]["a"], sku=sku, name=name, unit="kg",
                price=price, stock_quantity=50, is_active=True)
    db.add(p)
    db.commit()
    return p


def test_ambiguous_item_gets_pick_buttons_and_pick_resolves(db_session, linked, shop):  # noqa: F811
    _add_product(db_session, shop, "برجر لحم", "XX-01", 30)
    _add_product(db_session, shop, "برجر فراخ", "XX-02", 25)
    out = svc.handle_update(db_session, upd(500, "برجر"))   # two candidates, no winner
    assert out and out[0].buttons
    picks = [(t, c) for t, c in out[0].buttons if c.startswith("pick_") and c != "pick_none"]
    assert len(picks) == 2 and all(len(c) <= 64 for _, c in picks)   # Telegram limit
    assert any("برجر لحم" in t for t, _ in picks)
    assert ("مش من دول", "pick_none") in out[0].buttons
    target = next(c for t, c in picks if "برجر لحم" in t)
    out2 = tap(db_session, 500, target)
    assert out2 and out2[0].answer_callback_id
    assert "كام «برجر لحم»" in out2[0].text              # question moved on to quantity
    assert db_session.scalar(select(Order)) is None


def test_pick_none_skips_the_line(db_session, linked, shop):  # noqa: F811
    _add_product(db_session, shop, "برجر لحم", "XX-01", 30)
    _add_product(db_session, shop, "برجر فراخ", "XX-02", 25)
    out = svc.handle_update(db_session, upd(500, "برجر"))
    assert out and out[0].buttons
    out2 = tap(db_session, 500, "pick_none")
    assert out2 and "قصدك" not in out2[0].text
    assert db_session.scalar(select(Order)) is None


def test_stale_pick_gets_msg_stale(db_session, linked, shop):  # noqa: F811
    pid = _pid(db_session, "سكر 1 كيلو")
    out = tap(db_session, 500, f"pick_{pid}")   # no open question
    assert out and out[0].text == svc.chat_agent.MSG_STALE


def test_forged_and_foreign_callbacks_do_nothing(db_session, linked, shop, client):  # noqa: F811
    out = tap(db_session, 500, "evil_payload")  # whitelist rejects: acknowledged silently
    assert out[0].text == ""  # acknowledged silently, nothing executed
    # a random uuid that is not a candidate of any open question
    out = tap(db_session, 500, f"pick_{uuid.uuid4()}")
    assert out[0].text == svc.chat_agent.MSG_STALE
    # a product of the OTHER demo business can never be bought here
    other = db_session.scalar(select(Product).where(
        Product.business_id == shop["ids"]["b"]).limit(1))
    assert other is not None
    out = tap(db_session, 500, f"buy_{other.id}")
    assert out[0].text == svc.chat_agent.MSG_STALE


def test_cart_flow_with_buttons(db_session, linked, shop):  # noqa: F811
    out = svc.handle_update(db_session, upd(500, "سكر"))   # no qty -> the bot asks
    assert "كام" in out[0].text
    out = svc.handle_update(db_session, upd(500, "3"))
    assert "تأكيد" in out[0].text or "تمام" in out[0].text
    callbacks = {c for _, c in (out[0].buttons or [])}
    assert {"cart_confirm", "cart_cancel", "view_cart"} <= callbacks
    # view_cart is read-only and shows the draft
    out = tap(db_session, 500, "view_cart")
    assert "3 × سكر" in out[0].text
    assert db_session.scalar(select(Order)) is None
    # cart_confirm behaves like «تمام»
    out = tap(db_session, 500, "cart_confirm")
    assert "استلمنا طلبك" in out[0].text
    assert db_session.scalar(select(Order)) is not None


def test_cart_cancel_clears(db_session, linked):  # noqa: F811
    svc.handle_update(db_session, upd(500, "٣ سكر"))
    svc.handle_update(db_session, upd(500, "3"))
    out = tap(db_session, 500, "cart_cancel")
    assert "ألغيت" in out[0].text
    out = tap(db_session, 500, "view_cart")
    assert out[0].text == svc.chat_agent.MSG_EMPTY_CART


def test_callback_deduped_on_redelivery(db_session, linked, shop):  # noqa: F811
    upd_id = next(_ids)
    svc.handle_update(db_session, upd(500, "٣ سكر"))
    svc.handle_update(db_session, upd(500, "3"))
    first = tap(db_session, 500, "cart_confirm", update_id=upd_id)
    second = tap(db_session, 500, "cart_confirm", update_id=upd_id)
    assert "استلمنا طلبك" in first[0].text and second == []


def test_category_buttons_and_listing(db_session, linked, shop):  # noqa: F811
    # give the demo shop trial-style SKUs so the category map applies
    p = db_session.scalar(select(Product).where(Product.name == "سكر 1 كيلو"))
    p.sku = "MEAT-01"
    out = svc.handle_update(db_session, upd(500, "اهلا"))
    callbacks = {c for _, c in (out[0].buttons or [])}
    assert "cat_MEAT" in callbacks
    out = tap(db_session, 500, "cat_MEAT")
    assert "اللحوم" in out[0].text and "سكر 1 كيلو" in out[0].text
    out = tap(db_session, 500, "cat_NOPE")
    assert out[0].text == svc.chat_agent.MSG_STALE


def test_buy_button_behaves_like_typing_the_name(db_session, linked, shop):  # noqa: F811
    pid = _pid(db_session, "سكر 1 كيلو")
    out = tap(db_session, 500, f"buy_{pid}")
    assert "كام «سكر 1 كيلو»" in out[0].text


def test_stale_cart_buttons_get_msg_stale(db_session, linked):  # noqa: F811
    out = tap(db_session, 500, "cart_confirm")   # nothing to confirm
    assert out[0].text == svc.chat_agent.MSG_STALE
    out = tap(db_session, 500, "cart_cancel")    # no draft to cancel
    assert out[0].text == svc.chat_agent.MSG_STALE
    out = tap(db_session, 500, "view_cart")
    assert out[0].text == svc.chat_agent.MSG_EMPTY_CART


def test_unlinked_chat_gets_not_linked(db_session, shop):  # noqa: F811
    out = tap(db_session, 4242, "view_cart")
    assert out[0].text == svc.MSG_NOT_LINKED


def test_group_and_bot_callbacks_ignored(db_session, linked):  # noqa: F811
    assert tap(db_session, 500, "view_cart", chat_type="group") == []
    assert tap(db_session, 500, "view_cart", is_bot=True) == []
