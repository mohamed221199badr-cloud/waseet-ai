"""Measure the matching engine on the synthetic set.

    python -m tests.eval.evaluate          # prints the table
It needs no database: the matcher is pure.
"""

import uuid
from collections import Counter

from app.matching import CatalogEntry, Matcher
from tests.eval.dataset import CASES, CATALOG, HOLDOUT, X


def build():
    entries = [CatalogEntry(uuid.uuid4(), sku, name, list(al)) for sku, name, al in CATALOG]
    sku_of = {e.product_id: e.sku for e in entries}
    return Matcher(entries), sku_of


def run(cases=CASES) -> dict:
    matcher, sku_of = build()
    c: Counter = Counter()
    failures = []
    for text, expected in cases:
        results = matcher.match_text(text)
        message_ok = len(results) == len(expected)
        for i, exp in enumerate(expected):
            got = results[i] if i < len(results) else None
            if exp == X:
                c["x_total"] += 1
                if got is not None and got.status == "matched":
                    c["false_accept"] += 1
                    message_ok = False
                    failures.append(
                        ("FALSE_ACCEPT", text, exp, sku_of[got.product_id], got.quantity)
                    )
                else:
                    c["x_correct"] += 1
                continue
            sku, qty = exp
            c["p_total"] += 1
            if got is None or got.status != "matched":
                c["safe_miss"] += 1
                message_ok = False
                failures.append(("safe_miss", text, exp, got.reason if got else "no line", None))
            elif sku_of[got.product_id] != sku:
                c["wrong_product"] += 1
                message_ok = False
                failures.append(("WRONG_PRODUCT", text, exp, sku_of[got.product_id], got.quantity))
            elif got.quantity != qty:
                c["wrong_quantity"] += 1
                message_ok = False
                failures.append(("WRONG_QTY", text, exp, sku_of[got.product_id], got.quantity))
            else:
                c["p_correct"] += 1
        c["messages"] += 1
        c["messages_ok"] += int(message_ok)
    c["items"] = c["p_total"] + c["x_total"]
    return {"counts": c, "failures": failures}


def main() -> None:
    import sys

    r = run(HOLDOUT if "--holdout" in sys.argv else CASES)
    c = r["counts"]
    print(f"messages: {c['messages']}  fully correct: {c['messages_ok']} "
          f"({100 * c['messages_ok'] / c['messages']:.1f}%)")
    print(f"items expected to match: {c['p_total']}")
    pct = 100 * c["p_correct"] / c["p_total"]
    print(f"  correct product+quantity: {c['p_correct']} ({pct:.1f}%)")
    print(f"  safe miss (exception instead): {c['safe_miss']}")
    print(f"  wrong product: {c['wrong_product']}   wrong quantity: {c['wrong_quantity']}")
    print(f"items expected to be exceptions: {c['x_total']}")
    print(f"  correctly kept as exception: {c['x_correct']}   FALSE ACCEPT: {c['false_accept']}")
    for f in r["failures"]:
        print("  ", *f)


if __name__ == "__main__":
    main()
