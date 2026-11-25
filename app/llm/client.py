"""Optional Ollama client. Every failure returns None so callers fall back to the rules.

The model never decides facts: its output is only a hint that is validated against the
catalog and then confirmed by the customer. No customer data other than the one message
text is sent, and it goes to OLLAMA_URL only (default: local machine)."""

import json
import logging

import httpx

from app.config import settings

log = logging.getLogger(__name__)


def llm_enabled() -> bool:
    """assist and chat both allow product suggestions; chat additionally drives the dialogue."""
    return settings.llm_mode in ("assist", "chat")


def chat_mode() -> bool:
    return settings.llm_mode == "chat"


def chat_json(system: str, user: str, *, timeout: float | None = None,
              num_predict: int = 80, num_ctx: int = 1024) -> dict | None:
    """Ask Ollama for a JSON object. None on any error, timeout, or non-object answer."""
    if not llm_enabled():
        return None
    body = {
        "model": settings.ollama_model, "stream": False, "format": "json",
        "options": {"temperature": 0, "num_ctx": num_ctx, "num_predict": num_predict},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    try:
        r = httpx.post(
            settings.ollama_url.rstrip("/") + "/api/chat", json=body,
            timeout=timeout or settings.llm_timeout_seconds,
        )
        r.raise_for_status()
        out = json.loads(r.json()["message"]["content"])
    except Exception as exc:  # network, timeout, bad JSON: never break the order flow
        log.warning("llm unavailable: %s", type(exc).__name__)
        return None
    return out if isinstance(out, dict) else None


def suggest_product(raw_text: str, names: list[str]) -> int | None:
    """Index into `names` the model thinks the customer meant, or None. The caller must
    still ask the customer; a suggestion is never auto-accepted."""
    if not names:
        return None
    listing = "\n".join(f"{i + 1}: {n}" for i, n in enumerate(names))
    out = chat_json(
        "اختار رقم الصنف اللي العميل قصده من القائمة بس، أو null لو مفيش صنف مناسب. "
        'رد JSON بالشكل {"choice": رقم أو null}.\n' + listing,
        raw_text,
    )
    try:
        n = int(out["choice"]) if out and out.get("choice") is not None else None
    except (TypeError, ValueError):
        return None
    return n - 1 if n is not None and 1 <= n <= len(names) else None


def converse(names: list[str], user: str, pending: str | None = None) -> dict | None:
    """LLM_MODE=chat: one dialogue turn. Returns {"reply": str, "items": [(name, qty)]} or
    None (timeout, error, invalid output) so the caller falls back to the rules.
    The model only PROPOSES: items are re-validated by the matching engine against the DB,
    and a reply containing any digit or money word is rejected (prices, stock and quantities
    are written by code only)."""
    if not chat_mode():
        return None
    catalog = "، ".join(names[:30])
    system = (
        "انت بوت استلام طلبات لمحل جملة في مصر. اتكلم بالمصري، جمل قصيرة ومحترمة. "
        "مهمتك: تفهم رسالة العميل. لو العميل بيطلب أصناف، استخرجها في items (الاسم زي ما "
        "قاله والكمية رقم). لو بيسلم أو بيسأل سؤال بسيط، رد عليه بشكل ودود في reply وقوله "
        "يكتب الصنف والكمية. ممنوع تذكر أي سعر أو مخزون أو رقم في reply، ولا تأكد الطلب "
        "ولا تلغيه. الأصناف المتاحة: " + catalog + ". "
        "أمثلة. العميل: عايز اتنين رز وكيلو سكر -> "
        '{"reply": "", "items": [{"name": "رز", "qty": 2}, {"name": "سكر", "qty": 1}]} . '
        'العميل: السلام عليكم -> {"reply": "أهلاً بيك، تحب تطلب إيه؟", "items": []} . '
        'العميل: مين انت -> {"reply": "أنا بوت بستقبل طلبات المحل. اكتب الصنف والكمية.", '
        '"items": []} . '
        "لو مش طلب ولا سلام، خلي reply فاضي. ممنوع تقول إن حاجة متوفرة أو مش متوفرة، "
        "وممنوع تتكلم عن التوصيل أو المكان. رد JSON بس. "
        + (f" السؤال المفتوح مع العميل: {pending}" if pending else "")
    )
    out = chat_json(system, user, timeout=settings.llm_chat_timeout_seconds,
                    num_predict=160, num_ctx=1536)
    if not out:
        return None
    reply = out.get("reply")
    reply = reply.strip() if isinstance(reply, str) else ""
    if len(reply) > 300 or any(c.isdigit() or c in "٠١٢٣٤٥٦٧٨٩" for c in reply) \
            or any(w in reply for w in (
                "جنيه", "سعر", "مخزون", "تم تأكيد", "تم الغاء", "تم إلغاء", "متوفر", "نتوفر",
                "توفر", "توصيل", "نوصل", "بتوصل", "القاهرة", "مصر", "اعتذر", "أعتذر")):
        reply = ""
    items = []
    for it in (out.get("items") or [])[:20]:
        try:
            name, qty = str(it["name"]).strip(), int(float(it["qty"]))
        except (KeyError, TypeError, ValueError):
            continue
        if name and 0 < qty <= 100000:
            items.append((name, qty))
    return {"reply": reply, "items": items}
