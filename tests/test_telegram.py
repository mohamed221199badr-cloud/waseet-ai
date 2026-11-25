"""Telegram intake with MOCKED updates. No network and no bot token are used anywhere:
live Telegram is not covered by these tests."""

import itertools
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select

from app.models import AuditEvent
from app.models_orders import Order, TelegramLink, TelegramLinkCode
from app.telegram import client as tgclient
from app.telegram import service as svc
from tests.helpers import auth_headers
from tests.test_intake import shop  # noqa: F401
from tests.test_migrations import _alembic, scratch_url  # noqa: F401

_ids = itertools.count(1000)


def upd(chat_id, body, *, chat_type="private", is_bot=False, update_id=None):
    return {
        "update_id": update_id if update_id is not None else next(_ids),
        "message": {"message_id": 1, "text": body, "chat": {"id": chat_id, "type": chat_type},
                    "from": {"id": chat_id, "is_bot": is_bot}},
    }


def say(db, chat_id, body, **kw):
    out = svc.handle_update(db, upd(chat_id, body, **kw))
    return out[0].text if out else None


def issue(client, shop_, customer_id=None):  # noqa: F811
    r = client.post("/api/v1/telegram/link-codes", headers=shop_["h"],
                    json={"customer_id": customer_id or shop_["c"]["id"]})
    assert r.status_code == 201, r.text
    return r.json()["code"]


def test_link_then_order_creates_telegram_draft(client, db_session, shop):  # noqa: F811
    code = issue(client, shop)
    assert say(db_session, 111, f"/start {code}") == svc.MSG_LINKED
    reply = say(db_session, 111, "٣ سكر\n٥ بسكويت")
    assert "✓ 3 سكر" in reply and "؟ 5 بسكويت" in reply
    assert "28" not in reply  # no prices in the bot
    order = db_session.scalar(select(Order))
    assert order.channel == "telegram" and order.status == "draft"
    assert str(order.customer_id) == shop["c"]["id"]
    assert str(order.business_id) == shop["ids"]["a"].hex or order.business_id == shop["ids"]["a"]
    assert len(order.items) == 1 and len(order.exceptions) == 1
    # stock untouched
    stock = client.get(f"/api/v1/products/{shop['p']['SU']['id']}", headers=shop["h"]).json()
    assert stock["stock_quantity"] == 100


def test_unlinked_chat_gets_fixed_reply_and_no_order(client, db_session, shop):  # noqa: F811
    assert say(db_session, 222, "٣ سكر") == svc.MSG_NOT_LINKED
    assert say(db_session, 222, "/start") == svc.MSG_NOT_LINKED
    assert db_session.scalar(select(func.count()).select_from(Order)) == 0


def test_bad_code_is_rejected_and_code_is_single_use(client, db_session, shop):  # noqa: F811
    assert say(db_session, 333, "/start WRONGCODE1") == svc.MSG_BAD_CODE
    code = issue(client, shop)
    assert say(db_session, 333, f"/start {code.lower()}") == svc.MSG_LINKED  # case-insensitive
    assert say(db_session, 444, f"/start {code}") == svc.MSG_BAD_CODE  # already used
    assert say(db_session, 444, "٣ سكر") == svc.MSG_NOT_LINKED


def test_expired_code_rejected(client, db_session, shop):  # noqa: F811
    code = issue(client, shop)
    row = db_session.scalar(select(TelegramLinkCode))
    row.expires_at = datetime.utcnow() - timedelta(minutes=1)
    db_session.commit()
    assert say(db_session, 555, f"/start {code}") == svc.MSG_BAD_CODE


def test_code_is_stored_hashed(client, db_session, shop):  # noqa: F811
    code = issue(client, shop)
    row = db_session.scalar(select(TelegramLinkCode))
    assert code not in row.code_hash and row.code_hash == svc.hash_code(code)


def test_linked_chat_cannot_be_silently_relinked(client, db_session, shop):  # noqa: F811
    other = client.post("/api/v1/customers", headers=shop["h"], json={"name": "تاني"}).json()
    c1, c2 = issue(client, shop), issue(client, shop, other["id"])
    assert say(db_session, 666, f"/start {c1}") == svc.MSG_LINKED
    assert say(db_session, 666, f"/start {c2}") == svc.MSG_ALREADY_LINKED
    link = db_session.scalar(select(TelegramLink))
    assert str(link.customer_id) == shop["c"]["id"]
    assert say(db_session, 666, "/stop") == svc.MSG_UNLINKED
    assert say(db_session, 666, "٣ سكر") == svc.MSG_NOT_LINKED
    assert say(db_session, 666, f"/start {c2}") == svc.MSG_LINKED


def test_cross_tenant_isolation(client, db_session, shop, two_businesses):  # noqa: F811
    """A code from business A can only ever create orders in A; B's owner sees nothing of A."""
    code = issue(client, shop)
    say(db_session, 777, f"/start {code}")
    say(db_session, 777, "٣ سكر")
    hb = auth_headers(two_businesses["b"])
    assert client.get("/api/v1/orders", headers=hb).json() == []
    assert client.get("/api/v1/telegram/links", headers=hb).json() == []
    link_id = client.get("/api/v1/telegram/links", headers=shop["h"]).json()[0]["id"]
    assert client.delete(f"/api/v1/telegram/links/{link_id}", headers=hb).status_code == 404
    # B cannot issue a code for A's customer
    r = client.post("/api/v1/telegram/link-codes", headers=hb,
                    json={"customer_id": shop["c"]["id"]})
    assert r.status_code == 404


def test_message_text_cannot_choose_business_or_customer(client, db_session, shop):  # noqa: F811
    code = issue(client, shop)
    say(db_session, 888, f"/start {code}")
    biz_b = "00000000-0000-0000-0000-000000000000"
    say(db_session, 888, f"business_id={biz_b} customer_id={biz_b}\n٣ سكر")
    order = db_session.scalar(select(Order))
    assert order.business_id == shop["ids"]["a"]


def test_owner_revoke_stops_intake(client, db_session, shop):  # noqa: F811
    say(db_session, 999, f"/start {issue(client, shop)}")
    links = client.get("/api/v1/telegram/links", headers=shop["h"]).json()
    assert links[0]["customer_name"] == "عميل" and "chat_id" not in links[0]
    assert client.delete(f"/api/v1/telegram/links/{links[0]['id']}",
                         headers=shop["h"]).status_code == 204
    assert say(db_session, 999, "٣ سكر") == svc.MSG_NOT_LINKED


def test_inactive_customer_blocked(client, db_session, shop):  # noqa: F811
    say(db_session, 1001, f"/start {issue(client, shop)}")
    client.patch(f"/api/v1/customers/{shop['c']['id']}", headers=shop["h"],
                 json={"is_active": False})
    assert say(db_session, 1001, "٣ سكر") == svc.MSG_INACTIVE
    r = client.post("/api/v1/telegram/link-codes", headers=shop["h"],
                    json={"customer_id": shop["c"]["id"]})
    assert r.status_code == 409


def test_groups_bots_and_non_text_ignored(client, db_session, shop):  # noqa: F811
    assert svc.handle_update(db_session, upd(1, "٣ سكر", chat_type="group")) == []
    assert svc.handle_update(db_session, upd(1, "٣ سكر", is_bot=True)) == []
    assert svc.handle_update(db_session, {"update_id": 5, "message": {"chat": {
        "id": 1, "type": "private"}, "photo": []}}) == []
    assert svc.handle_update(db_session, {"update_id": 6}) == []
    assert svc.handle_update(db_session, {"nonsense": True}) == []


def test_redelivered_update_creates_one_order(client, db_session, shop):  # noqa: F811
    say(db_session, 1100, f"/start {issue(client, shop)}")
    u = upd(1100, "٣ سكر", update_id=424242)
    assert svc.handle_update(db_session, u)
    assert svc.handle_update(db_session, u) == []
    assert db_session.scalar(select(func.count()).select_from(Order)) == 1


def test_full_cycle_telegram_to_confirmed_via_api(client, db_session, shop):  # noqa: F811
    h = shop["h"]
    say(db_session, 1200, f"/start {issue(client, shop)}")
    say(db_session, 1200, "٣ سكر\n٣ رز")
    order = client.get("/api/v1/orders?status=draft", headers=h).json()[0]
    assert order["channel"] == "telegram"
    exc = order["exceptions"][0]
    assert client.post(f"/api/v1/orders/{order['id']}/submit", headers=h).status_code == 409
    r5 = shop["p"]["R5"]["id"]
    assert client.post(f"/api/v1/orders/{order['id']}/exceptions/{exc['id']}/resolve",
                       headers=h, json={"product_id": r5}).status_code == 200
    assert client.post(f"/api/v1/orders/{order['id']}/submit", headers=h).status_code == 200
    assert client.post(f"/api/v1/orders/{order['id']}/confirm", headers=h).status_code == 200
    r = client.get(f"/api/v1/products/{shop['p']['SU']['id']}", headers=h).json()
    assert r["stock_quantity"] == 97


def test_help_empty_and_overlong_text(client, db_session, shop):  # noqa: F811
    say(db_session, 1300, f"/start {issue(client, shop)}")
    assert say(db_session, 1300, "/help") == svc.MSG_HELP
    assert say(db_session, 1300, "/whatever") == svc.MSG_HELP
    assert say(db_session, 1300, "x" * 5001) == svc.MSG_TOO_LONG
    assert db_session.scalar(select(func.count()).select_from(Order)) == 0
    # junk text is never guessed into an item: it becomes a line for the shop to review
    reply = say(db_session, 1300, "!!!")
    assert "؟ !!!" in reply or reply == svc.MSG_NO_LINES
    assert not db_session.scalar(select(Order)).items if reply != svc.MSG_NO_LINES else True


def test_audit_trail_written(client, db_session, shop):  # noqa: F811
    say(db_session, 1400, f"/start {issue(client, shop)}")
    say(db_session, 1400, "٣ سكر")
    entities = {(e.entity, e.action) for e in db_session.scalars(select(AuditEvent))}
    assert ("telegram_link", "create") in entities and ("order", "create") in entities


# ---- Bot API client with a mocked transport (no network) --------------------------

TOKEN = "123456:FAKE-TEST-TOKEN"


def mock_client(handler):
    return tgclient.TelegramClient(TOKEN, httpx.Client(transport=httpx.MockTransport(handler)))


def test_client_requires_token():
    with pytest.raises(ValueError):
        tgclient.TelegramClient("")


def test_requests_are_not_logged_with_the_token(caplog):
    import logging

    def ok(request):
        return httpx.Response(200, json={"ok": True, "result": True})

    with caplog.at_level(logging.DEBUG):
        mock_client(ok).send_message(1, "hi")
    assert TOKEN not in caplog.text


def test_client_error_never_leaks_token():
    def boom(request):
        raise httpx.ConnectError(f"cannot reach {request.url}")

    c = mock_client(boom)
    with pytest.raises(tgclient.TelegramError) as ei:
        c.send_message(1, "hi")
    assert TOKEN not in str(ei.value) and "ConnectError" in str(ei.value)

    def rejected(request):
        return httpx.Response(200, json={"ok": False, "description": "Forbidden: bot was blocked"})

    with pytest.raises(tgclient.TelegramError) as ei:
        mock_client(rejected).send_message(1, "hi")
    assert TOKEN not in str(ei.value)


def test_process_batch_replies_and_next_offset(client, db_session, shop, monkeypatch):  # noqa: F811
    code = issue(client, shop)
    sent = []

    def handler(request):
        sent.append(__import__("json").loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": True})

    from sqlalchemy.orm import sessionmaker
    factory = sessionmaker(bind=db_session.get_bind())
    batch = [upd(2000, f"/start {code}", update_id=50), upd(2000, "٣ سكر", update_id=51),
             {"update_id": 52, "edited_message": {}}]
    nxt = tgclient.process_batch(mock_client(handler), batch, session_factory=factory)
    assert nxt == 53
    assert [m["chat_id"] for m in sent] == [2000, 2000]
    assert sent[0]["text"] == svc.MSG_LINKED and "استلمنا طلبك" in sent[1]["text"]


def test_process_batch_survives_a_failing_update(client, db_session, shop, monkeypatch):  # noqa: F811
    from sqlalchemy.orm import sessionmaker
    factory = sessionmaker(bind=db_session.get_bind())
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, json={"ok": True, "result": True})

    real = svc.handle_update

    def flaky(db, u):
        if u["update_id"] == 60:
            raise RuntimeError("boom")
        return real(db, u)

    monkeypatch.setattr(tgclient, "handle_update", flaky)
    nxt = tgclient.process_batch(mock_client(handler),
                                 [upd(3000, "x", update_id=60), upd(3000, "x", update_id=61)],
                                 session_factory=factory)
    assert nxt == 62 and len(calls) == 1


def test_m4_migration_step_down_and_up(scratch_url):  # noqa: F811
    from sqlalchemy import create_engine, inspect
    m4 = {"telegram_link_codes", "telegram_links", "telegram_updates"}
    assert _alembic(scratch_url, "upgrade", "head").returncode == 0
    eng = create_engine(scratch_url)
    assert m4 <= set(inspect(eng).get_table_names())
    assert _alembic(scratch_url, "downgrade", "m3_0001").returncode == 0
    assert not (m4 & set(inspect(eng).get_table_names()))
    assert _alembic(scratch_url, "upgrade", "head").returncode == 0
    eng.dispose()
