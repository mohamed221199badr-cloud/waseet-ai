"""Centralized callback_data codec (UI v1.8).

One module builds and parses every inline-button payload; handlers never assemble or
split raw strings themselves. Format: `act:arg[:arg...]` with short action codes so a
full UUID payload stays well under Telegram's 64-byte limit. Parsing validates shape
only; ownership and staleness are re-checked by the handlers against the linked
business's own data, exactly as before.

Actions:
    cats                      list menu categories (level 1)
    cat:<category>:p<page>    one category's items, 6 per page
    it:<item_id>              item picked -> variant buttons, or quantity when univariant
    var:<variant_id>          variant picked -> the numeric keypad
    npd:<digit>               numpad digit 0-9
    npdot                     numpad decimal point (weight items only)
    npb                       numpad backspace
    npc                       numpad confirm
    dv:<delivery|pickup>      onboarding: delivery vs pickup choice
    sc:<category>:p<page>     search within this category (return point kept)
    cart                      cart review message
    cadd                      add another item (back to categories, keep draft)
    cok                       confirm the order (same as «تمام»)
    cclr                      ask before clearing the cart
    cclr_yes / cclr_no        clear-cart confirmation answer
    ccancel                   cancel the order flow (same as «الغي»)
    srch                      global menu search (from the main menu)
    ckdel:<delivery|pickup>   checkout: delivery-type choice
    ckaddr:<keep|change>      checkout: reuse the saved address?
    ckok                      checkout: final confirm, send to the cashier
    ckno                      checkout: cancel checkout, keep the cart
    noop                      inert button (current page indicator)

Legacy slice-A payloads (cat_<PREFIX>, buy_, pick_, view_cart, cart_confirm, ...) stay
handled by the old whitelist until the legacy products path is retired; new menu-mode
keyboards emit only this codec.
"""

from dataclasses import dataclass

MAX_CALLBACK_BYTES = 64


@dataclass(frozen=True)
class Callback:
    action: str
    args: tuple[str, ...] = ()

    def encode(self) -> str:
        data = self.action if not self.args else f"{self.action}:{':'.join(self.args)}"
        if len(data.encode("utf-8")) > MAX_CALLBACK_BYTES:
            raise ValueError(f"callback_data too long: {data!r}")
        return data


def build(action: str, *args: object) -> str:
    return Callback(action, tuple(str(a) for a in args)).encode()


def parse(data: str) -> Callback | None:
    """Shape-validated parse. None for anything malformed, unknown, or oversized."""
    if not isinstance(data, str) or not data or len(data.encode("utf-8")) > MAX_CALLBACK_BYTES:
        return None
    parts = data.split(":")
    action, args = parts[0], tuple(parts[1:])
    if action not in _ARITY or _ARITY[action] != len(args):
        return None
    if action in ("cat", "sc") and (not args[1].startswith("p") or not args[1][1:].isdigit()):
        return None
    if action == "npd" and (len(args[0]) != 1 or not args[0].isdigit()):
        return None
    if action == "dv" and args[0] not in ("delivery", "pickup"):
        return None
    if action == "ckdel" and args[0] not in ("delivery", "pickup"):
        return None
    if action == "ckaddr" and args[0] not in ("keep", "change"):
        return None
    if action in ("it",) and not _is_uuid(args[0]):
        return None
    if action in ("var",) and not _is_uuid(args[0]):
        return None
    return Callback(action, args)


_ARITY = {
    "cats": 0, "cat": 2, "sc": 2, "it": 1, "var": 1,
    "npd": 1, "npdot": 0, "npb": 0, "npc": 0, "dv": 1,
    "cart": 0, "cadd": 0, "cok": 0, "cclr": 0, "cclr_yes": 0, "cclr_no": 0,
    "ccancel": 0, "noop": 0, "showmenu": 0, "contact": 0, "addr": 0, "home": 0, "done": 0,
    "srch": 0, "ckdel": 1, "ckaddr": 1, "ckok": 0, "ckno": 0,
}


def _is_uuid(s: str) -> bool:
    if len(s) != 36:
        return False
    try:
        import uuid
        uuid.UUID(s)
        return True
    except ValueError:
        return False
