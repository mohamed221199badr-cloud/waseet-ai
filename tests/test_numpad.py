"""Feature 1 (19758722): the interactive in-chat numeric keypad.

Digits, backspace, decimal point for weight, deterministic validation against
sold_by rules, state integrity (no double-add on repeated confirm), no LLM.
"""

from decimal import Decimal

from app.chat import callbacks as cb_codec
from tests.test_intake import shop  # noqa: F401
from tests.test_menu_buttons_v18 import (  # noqa: F401
    CHAT,
    _add_item,
    _flat,
    linked,
    menu,
    tap,
)
from tests.test_migrations import _alembic, scratch_url  # noqa: F401


def _type(db, *taps):
    rep = None
    for t in taps:
        rep = tap(db, t)
    return rep


def _digits(*ds):
    return [cb_codec.build("npd", d) for d in ds]


def test_every_digit_and_backspace(db_session, linked, menu):  # noqa: F811
    tap(db_session, cb_codec.build("cat", "اللحوم", "p0"))
    rep = tap(db_session, cb_codec.build("it", menu["meat"].id))
    assert "…" in rep.text  # empty input placeholder
    for d in range(10):  # every digit 0-9 accepted
        rep = tap(db_session, cb_codec.build("npd", d))
    # leading zero stripped: input is 1234567890 capped at 8 chars -> 12345678
    assert "▫️ 12345678" in rep.text
    rep = tap(db_session, cb_codec.build("npb"))
    assert "▫️ 1234567" in rep.text
    rep = _type(db_session, *([cb_codec.build("npb")] * 7))
    assert "▫️ …" in rep.text  # backspace past empty stays empty


def test_zero_and_over_precision_rejected(db_session, linked, menu):  # noqa: F811
    tap(db_session, cb_codec.build("it", menu["meat"].id))
    rep = _type(db_session, cb_codec.build("npd", 0), cb_codec.build("npc"))
    assert "مش صح" in rep.text
    # weight supports decimals but not beyond 3 places (Numeric(10,3))
    rep = _type(db_session, *(_digits(1) + [cb_codec.build("npdot")] + _digits(2, 3, 4, 5)),
                cb_codec.build("npc"))
    assert "مش صح" in rep.text
    # the selection survives failed confirmations: fixing the input still adds
    rep = _type(db_session, cb_codec.build("npb"), cb_codec.build("npc"))
    assert "مفروم احمر" in rep.text and "1.234" in rep.text


def test_decimal_weight_and_double_dot_guard(db_session, linked, menu):  # noqa: F811
    tap(db_session, cb_codec.build("it", menu["meat"].id))
    taps = [cb_codec.build("npdot")]  # leading dot becomes 0.
    rep = _type(db_session, *(taps + _digits(5)))
    assert "▫️ 0.5" in rep.text
    rep = tap(db_session, cb_codec.build("npdot"))  # second dot ignored
    assert "▫️ 0.5" in rep.text
    rep = tap(db_session, cb_codec.build("npc"))
    assert "225" in rep.text  # 0.5 x 450


def test_unit_rejects_fraction_even_forged(db_session, linked, menu):  # noqa: F811
    tap(db_session, cb_codec.build("cat", "مصنعات", "p0"))
    tap(db_session, cb_codec.build("it", menu["burger"].id))
    tap(db_session, cb_codec.build("var", menu["burger_v"][0].id))
    rep = tap(db_session, cb_codec.build("npdot"))  # forged: no dot button for units
    assert "✅ تأكيد" in _flat(rep.button_rows) and "▫️ …" in rep.text  # ignored, keypad kept
    rep = _type(db_session, *_digits(3), cb_codec.build("npc"))
    assert "840" in rep.text  # 3 x 280, selection intact after the forged tap


def test_repeated_confirm_never_adds_twice(db_session, linked, menu):  # noqa: F811
    tap(db_session, cb_codec.build("it", menu["meat"].id))
    _type(db_session, *_digits(2))
    rep = tap(db_session, cb_codec.build("npc"))
    assert "900" in rep.text  # 2 x 450, one line
    rep = tap(db_session, cb_codec.build("npc"))  # same keypad message tapped again
    assert "مش متاح" in rep.text
    rep = tap(db_session, cb_codec.build("cart"))
    assert rep.text.count("مفروم احمر") == 1 and "900" in rep.text


def test_min_quantity_enforced(db_session, linked, menu):  # noqa: F811
    biz = linked["ids"]["a"]
    item, _ = _add_item(db_session, biz, "اللحوم", "لحم ضأن",
                        [("عادي", Decimal("600.00"), True)],
                        sold_by="weight", unit_label="كيلو")
    from app.models_menu import MenuItem
    row = db_session.get(MenuItem, item.id)
    row.min_quantity = Decimal("0.500")
    db_session.commit()
    tap(db_session, cb_codec.build("cat", "اللحوم", "p0"))
    tap(db_session, cb_codec.build("it", item.id))
    rep = _type(db_session, cb_codec.build("npdot"), *_digits(2), cb_codec.build("npc"))
    assert "أقل كمية" in rep.text
    rep = _type(db_session, cb_codec.build("npb"), cb_codec.build("npd", 5),
                cb_codec.build("npc"))
    assert "0.5" in rep.text and "300" in rep.text


def test_back_button_leaves_keypad(db_session, linked, menu):  # noqa: F811
    tap(db_session, cb_codec.build("it", menu["meat"].id))
    _type(db_session, *_digits(4))
    rep = tap(db_session, cb_codec.build("it", menu["meat"].id))  # 🔙 رجوع row target
    assert "…" in rep.text  # fresh keypad, input reset
