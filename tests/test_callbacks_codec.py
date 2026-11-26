"""UI v1.8: the centralized callback codec (build/parse/validate, 64-byte budget)."""

import uuid

from app.chat.callbacks import MAX_CALLBACK_BYTES, build, parse


def test_roundtrip_all_actions():
    iid, vid = uuid.uuid4(), uuid.uuid4()
    cases = [
        ("cats", (), "cats"),
        ("cat", ("اللحوم", "p2"), "cat:اللحوم:p2"),
        ("it", (str(iid),), f"it:{iid}"),
        ("var", (str(vid),), f"var:{vid}"),
        ("wq", ("500",), "wq:500"),
        ("uq", ("3",), "uq:3"),
        ("qcustom", (), "qcustom"),
        ("cart", (), "cart"),
        ("cadd", (), "cadd"),
        ("cok", (), "cok"),
        ("cclr", (), "cclr"),
        ("cclr_yes", (), "cclr_yes"),
        ("cclr_no", (), "cclr_no"),
        ("ccancel", (), "ccancel"),
        ("noop", (), "noop"),
    ]
    for action, args, expected in cases:
        data = build(action, *args)
        assert data == expected
        assert len(data.encode("utf-8")) <= MAX_CALLBACK_BYTES
        cb = parse(data)
        assert cb is not None and cb.action == action and cb.args == args


def test_parse_rejects_malformed():
    assert parse("") is None
    assert parse("bogus") is None
    assert parse("cat") is None                      # missing page
    assert parse("cat:اللحوم") is None               # missing page
    assert parse("cat:اللحوم:2") is None             # page must be p<n>
    assert parse("cat:اللحوم:px") is None
    assert parse("wq:abc") is None                   # quantities are digits
    assert parse("uq:2.5") is None
    assert parse("it:not-a-uuid") is None
    assert parse(f"var:{uuid.uuid4()}:nope") is None
    assert parse("cok:extra") is None
    assert parse("x" * 65) is None                   # over the Telegram budget
    assert parse("legacy cat_MEAT format") is None


def test_build_refuses_overlong_payload():
    import pytest
    with pytest.raises(ValueError):
        build("cat", "ق" * 60, "p1")
