import json
import os
import subprocess
import sys
import time
import urllib.request

CAT = [
    (1, "سكر أبيض 1 كجم", "كرتونة 12"),
    (2, "أرز مصري 1 كجم", "شيكارة 10"),
    (3, "زيت عباد الشمس 800 مل", "كرتونة 12"),
    (4, "زيت ذرة 800 مل", "كرتونة 12"),
    (5, "مكرونة سباجتي 400 جم", "كرتونة 20"),
    (6, "مكرونة ريشة 400 جم", "كرتونة 20"),
    (7, "شاي العروسة 40 كيس", "كرتونة 24"),
    (8, "شاي ليبتون 100 كيس", "كرتونة 12"),
    (9, "صلصة طماطم 350 جم", "كرتونة 24"),
    (10, "عدس أصفر 1 كجم", "شيكارة 10"),
    (11, "فول مدمس معلب", "كرتونة 24"),
    (12, "دقيق فاخر 1 كجم", "شيكارة 10"),
    (13, "ملح ناعم 700 جم", "كرتونة 20"),
    (14, "لبن جهينة 1 لتر", "كرتونة 12"),
    (15, "جبنة رومي قطعة", "قطعة"),
]
catxt = "\n".join(f"{i}: {n} (الوحدة: {u})" for i, n, u in CAT)
SYS = f"""أنت مساعد استلام طلبات جملة. الكتالوج:
{catxt}
حلل رسالة العميل (بالعامية المصرية) وارجع JSON فقط بالشكل:
{{"items":[{{"product_id":رقم,"qty":رقم,"unit":"كرتونة|شيكارة|قطعة|كيلو"}}],"clarify":"سؤال بالعربي لو في غموض أو null"}}
لو المنتج غير محدد (مثلا فيه أكتر من نوع) أو الكمية غير واضحة، لا تخمن: ضع clarify بسؤال ولا تضف الصنف. لا تخترع منتجات خارج الكتالوج."""
# (msg, gold items {pid:qty}, should_clarify)
T = [
    ("عايز 5 كراتين سكر", {1: 5}, 0),
    ("محتاج ٣ شكاير رز وكرتونتين زيت دوار الشمس", {2: 3, 3: 2}, 0),
    ("ابعتلي 10 كرتونه مكرونه سباجتى", {5: 10}, 0),
    ("2 كرتونة شاي العروسه و1 كرتونة صلصة", {7: 2, 9: 1}, 0),
    ("هات شكارتين عدس و٤ كراتين فول", {10: 2, 11: 4}, 0),
    ("اديني كرتونة لبن جهينه وشيكارة دقيق", {14: 1, 12: 1}, 0),
    ("5 كرتونة زيت ذره يا باشا", {4: 5}, 0),
    ("عاوز ملح 3 كراتين ومكرونه ريشه 2 كرتونه", {13: 3, 6: 2}, 0),
    ("سكر ٢٠ كرتونة ورز ١٠ شكاير", {1: 20, 2: 10}, 0),
    ("ازيك يا ريس محتاج كرتونتين ليبتون", {8: 2}, 0),
    ("عايز زيت", {}, 1),
    ("ابعتلي مكرونة 5 كراتين", {}, 1),
    ("شاي كام كرتونة عندك؟ عايز شوية", {}, 1),
    ("هات سكر", {}, 1),
    ("محتاج شوية رز كده", {}, 1),
    ("عايز كام كرتونة من اللي خلص امبارح", {}, 1),
    ("3 كراتين زيت عباد الشمس وزيت كمان", {3: 3}, 1),
    ("سكر 5 كراتين و معاه شاي", {1: 5}, 1),
    ("كرتونتين عصير مانجو", {}, 1),
    ("5 شكاير رز و 3 كراتين بيبسي", {2: 5}, 0),
    ("سكر 4 كرتونه ورز شكارتين وعدس واحده والحساب كام", {1: 4, 2: 2, 10: 1}, 0),
    ("عاوز دقيق ٢ شيكارة وصلصه ٣ كرتونه", {12: 2, 9: 3}, 0),
    ("جبنه رومي ٥ قطع", {15: 5}, 0),
    ("ابعت ليبتون و العروسه كل واحد كرتونه", {8: 1, 7: 1}, 0),
    ("لبن جهينة 6 كراتين وملح كرتونتين وسكر ٣", {14: 6, 13: 2, 1: 3}, 0),
    ("عايز مكرونه", {}, 1),
    ("هاتلي عشر كراتين زيت ذره وخمس شكاير رز", {4: 10, 2: 5}, 0),
    ("كرتونه صلصه وفولل ٢ كرتونه", {9: 1, 11: 2}, 0),
    ("زيت عباد 800 كرتونتين", {3: 2}, 0),
    ("رز", {}, 1),
]


def rss():
    o = subprocess.run(
        "ps -eo rss,cmd | grep -E 'ollama' | grep -v grep | awk '{s+=$1} END{print s/1024}'",
        shell=True,
        capture_output=True,
        text=True,
    ).stdout
    return float(o or 0)


def call(model, msg):
    body = {
        "model": model,
        "stream": False,
        "format": "json",
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_ctx": 1024, "num_predict": 120},
        "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": msg}],
    }
    t = time.time()
    try:
        r = json.load(
            urllib.request.urlopen(
                urllib.request.Request(
                    "http://localhost:11434/api/chat",
                    json.dumps(body).encode(),
                    {"Content-Type": "application/json"},
                ),
                timeout=100,
            )
        )
        txt = r["message"]["content"]
    except Exception:
        txt = "ERR"
    return txt, time.time() - t, rss()


def fill(model, budget):
    f = f"cache_{model.replace(':', '_')}.jsonl"
    done = {json.loads(line)["i"] for line in open(f)} if os.path.exists(f) else set()
    t0 = time.time()
    for i, (msg, _gold, _sc) in enumerate(T):
        if i in done:
            continue
        if time.time() - t0 > budget:
            break
        txt, lat, r = call(model, msg)
        open(f, "a").write(
            json.dumps({"i": i, "txt": txt, "lat": lat, "rss": r}, ensure_ascii=False) + "\n"
        )
    return f


def summarize(model):
    f = f"cache_{model.replace(':', '_')}.jsonl"
    rows = {json.loads(line)["i"]: json.loads(line) for line in open(f)}
    ok = tot = clok = bad = hal = 0
    lat = []
    mr = 0
    for i, r in rows.items():
        msg, gold, sc = T[i]
        lat.append(r["lat"])
        mr = max(mr, r["rss"])
        try:
            j = json.loads(r["txt"])
            got = {}
            for it in j.get("items") or []:
                got[int(it["product_id"])] = int(float(it["qty"]))
            cl = bool(j.get("clarify"))
        except Exception:
            bad += 1
            got = {}
            cl = False
        hal += any(p not in range(1, 16) for p in got)
        for p, q in gold.items():
            tot += 1
            ok += got.get(p) == q
        clok += cl == bool(sc)
    L = sorted(lat)
    n = len(rows)
    return dict(
        model=model,
        done=n,
        item_exact=f"{ok}/{tot}",
        clarify_correct=f"{clok}/{n}",
        bad_json=bad,
        halluc_ids=hal,
        lat_median=round(L[n // 2], 1),
        lat_max=round(L[-1], 1),
        peak_rss_mb=round(mr),
    )


if __name__ == "__main__":
    m = sys.argv[1]
    fill(m, float(sys.argv[2]))
    print(json.dumps(summarize(m), ensure_ascii=False))
