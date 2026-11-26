"""Sound alert for new pending orders: which orders ring, and the page script."""

from datetime import datetime

from ui import alert

NOW = datetime(2026, 11, 26, 19, 0)


def _o(status, created):
    return {"status": status, "created_at": created}


def test_only_fresh_pending_orders_ring():
    orders = [
        _o("pending", "2026-11-26 18:50"),   # fresh
        _o("pending", "2026-11-26 16:01"),   # 179 min: still fresh
        _o("pending", "2026-11-26 15:59"),   # 181 min: stale test order, silent
        _o("confirmed", "2026-11-26 18:55"),  # handled
        _o("rejected", "2026-11-26 18:55"),
        _o("pending", "bad"),                # malformed time is skipped, not a crash
    ]
    got = alert.fresh_pending(orders, now=NOW)
    assert [o["created_at"] for o in got] == ["2026-11-26 18:50", "2026-11-26 16:01"]


def test_no_orders_no_ring():
    assert alert.fresh_pending([], now=NOW) == []


def test_script_carries_count_and_repeat_interval():
    html = alert.alert_html(3)
    assert "const COUNT = 3;" in html
    assert f"setInterval(tick, {alert.BEEP_EVERY_MS})" in html
    assert "S.ctx.resume()" in html          # sound starts only after a click
    assert "__COUNT__" not in html and "__EVERY__" not in html


def test_zero_count_stops_ringing():
    assert "const COUNT = 0;" in alert.alert_html(0)
