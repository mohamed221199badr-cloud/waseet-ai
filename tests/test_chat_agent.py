"""Chat agent (M5) with MOCKED Telegram updates and a MOCKED Ollama. No network, no model.
Live Telegram and a real model are not covered here."""

import json

import httpx
import pytest
from sqlalchemy import func, select

from app.config import settings
from app.llm import client as llm
from app.models_orders import ChatSession, Order, OrderException
from tests.test_intake import shop  # noqa: F401
from tests.test_migrations import _alembic, scratch_url  # noqa: F401
from tests.test_telegram import issue, say


@pytest.fixture(autouse=True)
def _agent_on(monkeypatch):
    monkeypatch.setattr(settings, "chat_agent_enabled", True)
    monkeypatch.setattr(settings, "llm_mode", "off")


@pytest.fixture()
def linked(client, db_session, shop):  # noqa: F811
    say(db_session, 500, f"/start {issue(client, shop)}")
    return shop


def orders(db):
    return list(db.scalars(select(Order)))


def test_clear_order_goes_to_confirmation_then_draft(db_session, linked):
    r = say(db_session, 500, "٣ سكر\n٢ ملح")
    assert "3 × سكر 1 كيلو = 84.00" in r and "2 × ملح ناعم 1 كيلو = 20.00" in r
    assert "الإجمالي: 104.00" in r
    assert orders(db_session) == []          # nothing is an order before "تمام"
    done = say(db_session, 500, "تمام")
    assert "استلمنا طلبك" in done
    o = orders(db_session)[0]
    assert o.status == "draft" and o.channel == "telegram" and len(o.items) == 2
    assert db_session.scalar(select(func.count()).select_from(ChatSession)) == 0


def test_ambiguous_product_asks_and_customer_picks(db_session, linked):
    q = say(db_session, 500, "٣ رز")
    assert "1) أرز مصري 1 كيلو" in q and "2) أرز مصري 5 كيلو" in q
    r = say(db_session, 500, "٢")
    assert "3 × أرز مصري 5 كيلو" in r and "185.00" in r
    say(db_session, 500, "ايوه")
    assert orders(db_session)[0].items[0].quantity == 3


def test_missing_quantity_is_asked_not_guessed(db_session, linked):
    q = say(db_session, 500, "سكر")
    assert "كام" in q and "سكر 1 كيلو" in q
    assert say(db_session, 500, "كلمة") == "ما فهمتش الكمية. اكتب رقم بس، مثال: 3"
    r = say(db_session, 500, "خمسه")
    assert "5 × سكر 1 كيلو" in r


def test_unknown_item_goes_to_owner_exceptions_not_invented(db_session, linked):
    r = say(db_session, 500, "٣ سكر\n٥ بسكويت")
    assert "بسكويت" in r and "المحل هيراجعها" in r
    say(db_session, 500, "تمام")
    o = orders(db_session)[0]
    assert len(o.items) == 1 and len(o.exceptions) == 1
    assert o.exceptions[0].raw_text == "5 بسكويت"


def test_nothing_matches_creates_no_order(db_session, linked):
    r = say(db_session, 500, "٥ بسكويت")
    assert "ما لقيتش أصناف" in r
    assert orders(db_session) == []


def test_cancel_at_any_step(db_session, linked):
    say(db_session, 500, "٣ رز")
    assert "ألغيت" in say(db_session, 500, "الغي")
    say(db_session, 500, "٣ سكر")
    assert "ألغيت" in say(db_session, 500, "لا")
    assert orders(db_session) == []


def test_add_more_items_before_confirming(db_session, linked):
    say(db_session, 500, "٣ سكر")
    r = say(db_session, 500, "٢ ملح")
    assert "3 × سكر" in r and "2 × ملح" in r
    say(db_session, 500, "تمام")
    assert len(orders(db_session)[0].items) == 2


def test_customer_price_and_stock_warning_come_from_db(client, db_session, linked):
    h, c, p = linked["h"], linked["c"], linked["p"]
    client.put(f"/api/v1/customers/{c['id']}/prices/{p['SU']['id']}", headers=h,
               json={"price": "25.00"})
    assert "= 75.00 (السعر 25.00)" in say(db_session, 500, "٣ سكر")
    say(db_session, 500, "الغي")
    r = say(db_session, 500, "١٥٠ سكر")   # stock is 100
    assert "ممكن مش متوفرة" in r
    stock = client.get(f"/api/v1/products/{p['SU']['id']}", headers=h).json()["stock_quantity"]
    assert stock == 100                      # the agent never touches stock


def test_prices_can_be_hidden(db_session, linked, monkeypatch):
    monkeypatch.setattr(settings, "chat_show_prices", False)
    r = say(db_session, 500, "٣ سكر")
    assert "84" not in r and "الإجمالي" not in r


def test_session_expires(db_session, linked, monkeypatch):
    say(db_session, 500, "٣ رز")
    monkeypatch.setattr(settings, "chat_session_ttl_minutes", -1)
    r = say(db_session, 500, "تمام")           # stale session is dropped; "تمام" is not an order
    assert orders(db_session) == []
    assert "مش فاهم" in r


def test_three_unclear_answers_hand_the_line_to_the_owner(db_session, linked):
    say(db_session, 500, "٣ سكر\n٣ رز")
    for _ in range(2):
        assert "ما فهمتش" in say(db_session, 500, "مش عارف")
    r = say(db_session, 500, "مش عارف")
    assert "3 × سكر" in r and "المحل هيراجعها" in r
    say(db_session, 500, "تمام")
    o = orders(db_session)[0]
    assert o.exceptions[0].reason == "customer_unclear"


def test_tenant_comes_from_the_link_not_the_text(client, db_session, linked, two_businesses):
    say(db_session, 500, "٣ منتج ب أو شركة ب B-001")
    say(db_session, 500, "الغي")
    assert orders(db_session) == []
    assert db_session.scalar(select(func.count()).select_from(OrderException)) == 0


def test_redelivery_is_idempotent(db_session, linked):
    from app.telegram import service as svc
    from tests.test_telegram import upd
    u = upd(500, "٣ سكر", update_id=777)
    assert svc.handle_update(db_session, u)
    assert svc.handle_update(db_session, u) == []


# ---- optional LLM (Ollama mocked) -----------------------------------------------------
def _fake_ollama(monkeypatch, payload=None, exc=None):
    calls = []

    def post(url, json=None, timeout=None):
        calls.append((url, timeout))
        if exc:
            raise exc
        req = httpx.Request("POST", url)
        return httpx.Response(200, request=req, json={"message": {"content": payload}})

    monkeypatch.setattr(llm.httpx, "post", post)
    return calls


def test_llm_off_never_calls_ollama(monkeypatch):
    calls = _fake_ollama(monkeypatch, "{}")
    assert llm.chat_json("s", "u") is None and calls == []


def test_llm_errors_fall_back_to_none(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "assist")
    _fake_ollama(monkeypatch, exc=httpx.ConnectError("down"))
    assert llm.chat_json("s", "u") is None
    _fake_ollama(monkeypatch, "not json")
    assert llm.chat_json("s", "u") is None
    _fake_ollama(monkeypatch, "[1,2]")
    assert llm.chat_json("s", "u") is None


def test_llm_uses_configured_url_and_timeout(monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "assist")
    monkeypatch.setattr(settings, "ollama_url", "http://box:11434/")
    calls = _fake_ollama(monkeypatch, "{}")
    llm.chat_json("s", "u")
    assert calls == [("http://box:11434/api/chat", 8.0)]


def test_llm_suggestion_is_validated_and_still_asks_the_customer(db_session, linked, monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "assist")
    # model "hallucinates" an index outside the list: ignored
    _fake_ollama(monkeypatch, json.dumps({"choice": 99}))
    assert "ما لقيتش أصناف" in say(db_session, 500, "٣ سكرر ابيض ناعم جدا")
    # a valid suggestion becomes a question, never a silent pick
    from app.chat import agent as chat_agent
    monkeypatch.setattr(chat_agent, "MIN_SUGGEST_SIMILARITY", 0)
    _fake_ollama(monkeypatch, json.dumps({"choice": 1}))
    q = say(db_session, 500, "٣ بسكويت")
    assert "قصدك" in q and "اكتب 1 للتأكيد" in q
    assert orders(db_session) == []
    r = say(db_session, 500, "0")
    assert "ما لقيتش أصناف" in r


def test_llm_down_in_assist_mode_equals_rules_only(db_session, linked, monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "assist")
    _fake_ollama(monkeypatch, exc=httpx.ReadTimeout("slow"))
    r = say(db_session, 500, "٣ سكر\n٥ بسكويت")
    assert "3 × سكر" in r and "بسكويت" in r


def test_m5_migration_step_down_and_up(scratch_url):  # noqa: F811
    from sqlalchemy import create_engine, inspect
    assert _alembic(scratch_url, "upgrade", "head").returncode == 0
    eng = create_engine(scratch_url)
    assert "chat_sessions" in inspect(eng).get_table_names()
    assert _alembic(scratch_url, "downgrade", "m4_0001").returncode == 0
    assert "chat_sessions" not in inspect(eng).get_table_names()
    assert _alembic(scratch_url, "upgrade", "head").returncode == 0
    eng.dispose()


def test_one_sentence_with_conjunction_and_dual_form(db_session, linked):
    r = say(db_session, 500, "٣ شكاير رز 1 كيلو وكرتونتين ملح")
    assert "3 × أرز مصري 1 كيلو" in r and "2 × ملح ناعم 1 كيلو" in r


def test_web_chat_endpoint_uses_same_agent_and_is_tenant_scoped(client, shop):  # noqa: F811
    h, c = shop["h"], shop["c"]
    def send(text):
        return client.post("/api/v1/chat/message", headers=h,
                           json={"customer_id": c["id"], "text": text})

    r = send("٣ سكر")
    assert r.status_code == 200 and "3 × سكر 1 كيلو" in r.json()["reply"]
    r = send("تمام")
    assert "استلمنا طلبك" in r.json()["reply"]
    assert client.post("/api/v1/chat/reset", headers=h,
                       json={"customer_id": c["id"], "text": "x"}).status_code == 200
    import uuid
    bad = client.post("/api/v1/chat/message", headers=h,
                      json={"customer_id": str(uuid.uuid4()), "text": "٣ سكر"})
    assert bad.status_code == 404
    assert client.post("/api/v1/chat/message",
                       json={"customer_id": c["id"], "text": "x"}).status_code in (401, 403)


def test_llm_can_never_confirm_or_cancel_an_order(db_session, linked, monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "assist")
    calls = _fake_ollama(monkeypatch, json.dumps({"label": "no", "choice": None}))
    say(db_session, 500, "٣ سكر")
    r = say(db_session, 500, "ممم مش عارف")       # not yes/no: the order must stay open
    assert "اكتب «تمام»" in r and orders(db_session) == []
    assert db_session.scalar(select(func.count()).select_from(ChatSession)) == 1
    assert calls == [] or all("اختار رقم" not in str(c) for c in calls)


def test_non_order_messages_get_natural_different_replies(db_session, linked):
    seen = set()
    for text in ["السلام عليكم", "شكرا", "الأصناف", "كام سعر السكر", "ايه ده", "مساعدة"]:
        seen.add(say(db_session, 500, text))
    assert len(seen) == 6                       # never the same canned reply
    assert "سكر 1 كيلو" in say(db_session, 500, "الأصناف")
    assert "28.00" in say(db_session, 500, "بكام السكر")
    assert orders(db_session) == []
    assert db_session.scalar(select(func.count()).select_from(OrderException)) == 0


def test_greeting_in_the_middle_does_not_trap_or_corrupt_the_order(db_session, linked):
    q = say(db_session, 500, "٣ رز")
    r = say(db_session, 500, "مرحبا")
    assert "أهلاً بيك" in r and q in r          # greeting answered, question repeated
    r = say(db_session, 500, "٢ سكر")           # customer moves on: new item added
    assert "قصدك" in r or "2 × سكر" in r
    say(db_session, 500, "الغي")
    say(db_session, 500, "٣ سكر")
    r = say(db_session, 500, "شكرا")
    assert "العفو" in r and "3 × سكر" in r      # still confirming, order intact
    assert "استلمنا" in say(db_session, 500, "تمام")


def test_llm_never_suggests_for_chatter_or_dissimilar_words(db_session, linked, monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "assist")
    calls = _fake_ollama(monkeypatch, json.dumps({"choice": 1}))
    say(db_session, 500, "Hi")
    assert calls == []                          # no quantity -> model not even asked
    r = say(db_session, 500, "٢ مكرونه سباجتي")  # model says item 1, but it looks nothing alike
    assert "قصدك" not in r


# ---- LLM_MODE=chat (Ollama mocked) ------------------------------------------------------
def _chat_mode(monkeypatch, payload):
    monkeypatch.setattr(settings, "llm_mode", "chat")
    body = payload if isinstance(payload, str) else json.dumps(payload)
    return _fake_ollama(monkeypatch, body)


def test_chat_mode_greeting_uses_model_reply(db_session, linked, monkeypatch):
    _chat_mode(monkeypatch, {"reply": "أهلاً بيك، تحب تطلب إيه؟", "items": []})
    assert say(db_session, 500, "ازيك عامل ايه") == "أهلاً بيك، تحب تطلب إيه؟"


def test_chat_mode_rejects_unsafe_model_replies(db_session, linked, monkeypatch):
    for bad in ("السكر بـ 30 جنيه", "السكر ٣٠", "تم تأكيد طلبك", "للأسف مش متوفر"):
        _chat_mode(monkeypatch, {"reply": bad, "items": []})
        r = say(db_session, 500, "ازيك عامل ايه")
        assert bad not in r                     # falls back to the rule reply


def test_chat_mode_items_are_validated_by_the_engine_and_db(db_session, linked, monkeypatch):
    _chat_mode(monkeypatch, {"reply": "", "items": [
        {"name": "سكر", "qty": 3}, {"name": "ماكينة حلاقة", "qty": 2}]})
    r = say(db_session, 500, "ممكن تبعتلي الحاجة الحلوة تلاتة وماكينة حلاقة اتنين")
    assert "3 × سكر 1 كيلو = 84.00" in r      # price from DB, not from the model
    assert "ماكينة" in r and "المحل هيراجعها" in r  # unknown product never invented
    assert orders(db_session) == []             # model cannot create the order
    assert "استلمنا" in say(db_session, 500, "تمام")  # only the customer's "تمام" does


def test_chat_mode_untrusted_quantity_is_asked_not_used(db_session, linked, monkeypatch):
    _chat_mode(monkeypatch, {"reply": "", "items": [{"name": "سكر", "qty": 40}]})
    r = say(db_session, 500, "عايز سكر كتير")   # the customer never wrote 40
    assert "كام" in r and "40" not in r


def test_chat_mode_model_cannot_confirm_or_cancel(db_session, linked, monkeypatch):
    say(db_session, 500, "٣ سكر")
    _chat_mode(monkeypatch, {"reply": "تم تأكيد الطلب", "items": []})
    r = say(db_session, 500, "ممم")
    assert "تم تأكيد" not in r and orders(db_session) == []


def test_chat_mode_down_or_slow_falls_back_to_rules(db_session, linked, monkeypatch):
    monkeypatch.setattr(settings, "llm_mode", "chat")
    calls = _fake_ollama(monkeypatch, exc=httpx.ReadTimeout("slow"))
    r = say(db_session, 500, "السلام عليكم")
    assert "أهلاً بيك" in r                     # rule greeting
    assert calls and calls[0][1] == settings.llm_chat_timeout_seconds
    assert "3 × سكر" in say(db_session, 500, "٣ سكر")
