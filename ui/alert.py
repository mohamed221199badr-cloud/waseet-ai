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
CONFIRMED_MAX_AGE_MIN = 30  # auto-confirmed orders ring until acknowledged, for this long
REFRESH_SECONDS = 15
BEEP_EVERY_MS = 4000


def ringing(orders, acked=(), now: datetime | None = None) -> list:
    """Orders that should ring: new and not yet acknowledged by the cashier.

    Pending ones (waiting for the cashier) ring for up to ALERT_MAX_AGE_MIN minutes, so old
    test orders stay quiet. Confirmed ones (the bot confirmed them itself) ring for
    CONFIRMED_MAX_AGE_MIN minutes. Rejected and draft orders never ring."""
    now = (now or datetime.now(CAIRO)).replace(tzinfo=None)
    out = []
    for o in orders:
        age_limit = {"pending": ALERT_MAX_AGE_MIN, "confirmed": CONFIRMED_MAX_AGE_MIN}.get(
            o.get("status"))
        if age_limit is None or o.get("id") in acked:
            continue
        try:
            created = datetime.strptime(o["created_at"], "%Y-%m-%d %H:%M")
        except (KeyError, ValueError):
            continue
        if created >= now - timedelta(minutes=age_limit):
            out.append(o)
    return out


_JS = """
<div dir="rtl" style="font-family:sans-serif">
<button id="b" style="padding:8px 14px;font-size:15px;border-radius:8px;border:1px solid #ccc;cursor:pointer"></button>
</div>
<script>
const COUNT = __COUNT__;
let P = window; try { P = window.parent; void P.document; } catch (e) { P = window; }
// The siren logic is installed into the parent page once, so it keeps running when
// Streamlit replaces this small iframe on every refresh.
if (!P.__waseetAlert || P.__waseetAlert.v !== 2) {
  if (P.__waseetAlert && P.__waseetAlert.timer) P.clearInterval(P.__waseetAlert.timer);
  const sc = P.document.createElement('script');
  sc.textContent = `(function(){
    const S = window.__waseetAlert = {v: 2, on: false, n: 0, ctx: null, timer: null, title: document.title};
    S.beep = function() {
      try {
        const c = S.ctx, t0 = c.currentTime;
        const master = c.createGain(); master.gain.value = 2.5;
        const comp = c.createDynamicsCompressor();
        comp.threshold.value = -40; comp.knee.value = 0; comp.ratio.value = 20;
        comp.attack.value = 0.001; comp.release.value = 0.05;
        master.connect(comp); comp.connect(c.destination);
        for (let i = 0; i < 4; i++) {
          const s0 = t0 + i * 0.4;
          [['square', 1], ['sawtooth', 2]].forEach(function(x) {
            const o = c.createOscillator(), g = c.createGain();
            o.type = x[0];
            o.frequency.setValueAtTime(700 * x[1], s0);
            o.frequency.linearRampToValueAtTime(1500 * x[1], s0 + 0.36);
            g.gain.value = 1.0;
            o.connect(g); g.connect(master);
            o.start(s0); o.stop(s0 + 0.38);
          });
        }
      } catch (e) {}
    };
    S.tick = function() {
      if (S.n > 0) { document.title = '🔔 (' + S.n + ') طلب جديد'; if (S.on) S.beep(); }
      else { document.title = S.title; }
    };
    S.enable = function() {
      if (!S.ctx) S.ctx = new (window.AudioContext || window.webkitAudioContext)();
      S.ctx.resume(); S.on = true; S.beep();
    };
    S.timer = setInterval(S.tick, __EVERY__);
  })();`;
  P.document.head.appendChild(sc);
}
const S = P.__waseetAlert;
S.n = COUNT;
const b = document.getElementById('b');
function label() { b.textContent = S.on ? '🔊 صوت التنبيه شغال (اضغط للتجربة)' : '🔇 اضغط هنا لتفعيل صوت التنبيه'; }
b.onclick = () => { S.enable(); label(); };
label();
P.document.title = S.n > 0 ? '🔔 (' + S.n + ') طلب جديد' : S.title;
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
