"""Same synthetic Egyptian-Arabic messages as llm_bench.py, run through the rules engine only.
Run from the repo root:  python scripts/rules_bench.py   (no DB, no model needed)."""

import sys
import time
import uuid

sys.path.insert(0, "scripts")
from llm_bench import CAT, T

from app.chat.agent import _CONJ
from app.matching import CatalogEntry, Matcher

ids = {i: uuid.uuid4() for i, _, _ in CAT}
back = {v: k for k, v in ids.items()}
# aliases an owner would realistically add (short everyday names)
AL = {
    1: ["سكر"],
    2: ["رز"],
    3: ["زيت عباد الشمس"],
    4: ["زيت ذرة"],
    5: ["مكرونة سباجتي"],
    6: ["مكرونة ريشة"],
    7: ["شاي العروسة"],
    8: ["ليبتون"],
    9: ["صلصة"],
    10: ["عدس"],
    11: ["فول"],
    12: ["دقيق"],
    13: ["ملح"],
    14: ["لبن جهينة"],
    15: ["جبنة رومي"],
}
m = Matcher([CatalogEntry(ids[i], str(i), n, AL[i]) for i, n, _ in CAT])
ok = tot = clar_ok = 0
lat = []
for msg, gold, should in T:
    t = time.perf_counter()
    res = []
    for r in m.match_text(msg):  # same conjunction fallback as the chat agent
        parts = [x.strip() for x in _CONJ.split(r.raw) if x and x.strip()]
        alt = [y for x in parts for y in m.match_text(x)] if len(parts) > 1 else []
        ok_alt = alt and len(alt) == len(parts) and all(y.product_id for y in alt)
        res.extend(alt if r.status != "matched" and ok_alt else [r])
    lat.append(time.perf_counter() - t)
    got = {back[r.product_id]: r.quantity for r in res if r.status == "matched"}
    for p, q in gold.items():
        tot += 1
        ok += got.get(p) == q
    asks = any(r.status != "matched" for r in res)
    clar_ok += asks == bool(should)
print(
    f"messages={len(T)} item_exact={ok}/{tot} ask_or_not_correct={clar_ok}/{len(T)} "
    f"median_ms={sorted(lat)[len(lat) // 2] * 1000:.1f}"
)
