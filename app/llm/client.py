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
    return settings.llm_mode == "assist"


def chat_json(system: str, user: str) -> dict | None:
    """Ask Ollama for a JSON object. None on any error, timeout, or non-object answer."""
    if not llm_enabled():
        return None
    body = {
        "model": settings.ollama_model, "stream": False, "format": "json",
        "options": {"temperature": 0, "num_ctx": 1024, "num_predict": 80},
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    try:
        r = httpx.post(
            settings.ollama_url.rstrip("/") + "/api/chat", json=body,
            timeout=settings.llm_timeout_seconds,
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


def classify_reply(text: str, awaiting: str) -> str | None:
    """yes | no | other, for a reply the rules could not read."""
    out = chat_json(
        f'العميل بيرد على سؤال ({awaiting}). صنّف ردّه: yes أو no أو other. '
        'رد JSON {"label": "yes|no|other"}.', text,
    )
    label = out.get("label") if out else None
    return label if label in ("yes", "no", "other") else None
