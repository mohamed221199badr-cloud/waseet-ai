"""Sound alert for new orders: which orders ring, acknowledgement, and the page script."""

from datetime import datetime

from ui import alert

NOW = datetime(2026, 11, 26, 19, 0)


def _o(oid, status, created):
    return {"id": oid, "status": status, "created_at": created}


def test_pending_rings_for_three_hours_confirmed_for_thirty_minutes():
    orders = [
        _o("p1", "pending", "2026-11-26 18:50"),
        _o("p2", "pending", "2026-11-26 16:01"),    # 179 min: still rings
        _o("p3", "pending", "2026-11-26 15:59"),    # 181 min: stale test order, silent
        _o("c1", "confirmed", "2026-11-26 18:35"),  # 25 min, bot auto-confirmed: rings
        _o("c2", "confirmed", "2026-11-26 18:29"),  # 31 min: silent
        _o("r1", "rejected", "2026-11-26 18:55"),
        _o("d1", "draft", "2026-11-26 18:55"),
        _o("x1", "pending", "bad"),                 # malformed time is skipped, no crash
    ]
    got = alert.ringing(orders, now=NOW)
    assert [o["id"] for o in got] == ["p1", "p2", "c1"]


def test_acknowledged_orders_stop_ringing_but_new_ones_still_ring():
    orders = [_o("a", "confirmed", "2026-11-26 18:55"), _o("b", "pending", "2026-11-26 18:56")]
    assert [o["id"] for o in alert.ringing(orders, acked={"a"}, now=NOW)] == ["b"]
    assert alert.ringing(orders, acked={"a", "b"}, now=NOW) == []


def test_no_orders_no_ring():
    assert alert.ringing([], now=NOW) == []


def test_script_carries_count_siren_and_repeat_interval():
    html = alert.alert_html(3)
    assert "const COUNT = 3;" in html
    assert f"setInterval(S.tick, {alert.BEEP_EVERY_MS})" in html
    assert "S.ctx.resume()" in html          # sound starts only after a click
    assert "createDynamicsCompressor" in html and "sawtooth" in html
    assert "__COUNT__" not in html and "__EVERY__" not in html


def test_zero_count_stops_ringing():
    assert "const COUNT = 0;" in alert.alert_html(0)
