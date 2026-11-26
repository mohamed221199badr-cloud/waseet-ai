# ruff: noqa: E501
"""Loud sound alert for new pending orders on the orders page.

Browsers block sound until the user clicks once, so the page shows an enable button.
The beep timer lives on the parent window, so it survives the 15 s auto-refresh and
keeps ringing every few seconds until no fresh pending order is left."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit.components.v1 as components

CAIRO = ZoneInfo("Africa/Cairo")
ALERT_MAX_AGE_MIN = 180   # older pending orders (stale tests) do not ring forever
REFRESH_SECONDS = 15
BEEP_EVERY_MS = 4000


def fresh_pending(orders, now: datetime | None = None) -> list:
    """Pending orders created within the last ALERT_MAX_AGE_MIN minutes (Cairo time)."""
    now = (now or datetime.now(CAIRO)).replace(tzinfo=None)
    limit = now - timedelta(minutes=ALERT_MAX_AGE_MIN)
    out = []
    for o in orders:
        if o.get("status") != "pending":
            continue
        try:
            created = datetime.strptime(o["created_at"], "%Y-%m-%d %H:%M")
        except (KeyError, ValueError):
            continue
        if created >= limit:
            out.append(o)
    return out


_JS = """
<div dir="rtl" style="font-family:sans-serif">
<button id="b" style="padding:8px 14px;font-size:15px;border-radius:8px;border:1px solid #ccc;cursor:pointer"></button>
</div>
<script>
const COUNT = __COUNT__;
let P = window; try { P = window.parent; void P.document; } catch (e) { P = window; }
const S = P.__waseetAlert = P.__waseetAlert || {on: false, n: 0, ctx: null, timer: null, title: P.document.title};
S.n = COUNT;
function beep() {
  try {
    const c = S.ctx, t0 = c.currentTime;
    [880, 660, 880, 660].forEach((f, i) => {
      const o = c.createOscillator(), g = c.createGain();
      o.type = 'square'; o.frequency.value = f;
      g.gain.setValueAtTime(0.9, t0 + i * 0.22);
      g.gain.setValueAtTime(0, t0 + i * 0.22 + 0.18);
      o.connect(g); g.connect(c.destination);
      o.start(t0 + i * 0.22); o.stop(t0 + i * 0.22 + 0.2);
    });
  } catch (e) {}
}
function tick() {
  if (S.n > 0) { P.document.title = '🔔 (' + S.n + ') طلب جديد'; if (S.on) beep(); }
  else { P.document.title = S.title; }
}
if (!S.timer) S.timer = P.setInterval(tick, __EVERY__);
const b = document.getElementById('b');
function label() { b.textContent = S.on ? '🔊 صوت التنبيه شغال (اضغط للتجربة)' : '🔇 اضغط هنا لتفعيل صوت التنبيه'; }
b.onclick = () => {
  if (!S.ctx) S.ctx = new (P.AudioContext || P.webkitAudioContext)();
  S.ctx.resume(); S.on = true; beep(); label();
};
label();
if (S.n > 0 && S.on) beep();
</script>
"""


def alert_html(count: int) -> str:
    return (_JS.replace("__COUNT__", json.dumps(int(count)))
            .replace("__EVERY__", str(BEEP_EVERY_MS)))


def render(count: int) -> None:
    components.html(alert_html(count), height=56)


def silence() -> None:
    """Other pages: stop ringing (the timer keeps running but the count is zero)."""
    components.html(alert_html(0).replace("<button", "<button hidden"), height=0)
