"""One-off bot-trial import: his real menu (Telegram 18934527) into the OLD products
table so the Phase 1 bot (which still reads products, per v3 B2) can answer customers.

Rules (agreed shape, pending his final yes):
- 84 single-price items -> 1 product each.
- 9 multi-price items (7x two-price, 2x three-price) -> one product PER PRICE, with a
  placeholder variant suffix (سعر 1/2/3) because variant names are still his open call.
- كبيبة شامي (NULL price) is SKIPPED and listed.
- unit: kg for اللحوم (he said meats sell by weight); piece elsewhere (provisional -
  sold_by for the other categories is one of his parked questions).
- Services (kind: service) are imported as products too, same table.

Dry run by default: prints the exact rows, writes NOTHING.
Apply: --database-url URL --business-slug SLUG --commit
"""
from __future__ import annotations

import argparse
import sys

# (category, name, [prices], kind) - transcribed verbatim from his message 18934527.
MENU = [
    ("اللحوم", "مكعبات كباب حلة", [430, 450], "goods"),
    ("اللحوم", "مكعبات لحم خضار احمر", [450], "goods"),
    ("اللحوم", "مكعبات لحم احمر مقشر", [450], "goods"),
    ("اللحوم", "مفروم احمر", [450], "goods"),
    ("اللحوم", "مفروم متوسط الدسم", [400, 420], "goods"),
    ("اللحوم", "مفروم حواوشي", [300], "goods"),
    ("اللحوم", "استريكوت استيك", [430, 450], "goods"),
    ("اللحوم", "ريب اي استيك", [430, 450], "goods"),
    ("اللحوم", "قشرة لوز", [430, 450], "goods"),
    ("اللحوم", "رق فلتتو", [450], "goods"),
    ("اللحوم", "رق التريانكو", [450], "goods"),
    ("اللحوم", "الوزة", [430, 450], "goods"),
    ("اللحوم", "بوفتيك", [450], "goods"),
    ("اللحوم", "كبدة ضاني", [480], "goods"),
    ("اللحوم", "كبيبة شامي", [], "goods"),  # NULL price -> skipped
    ("اللحوم", "كوارع متبلة", [350], "goods"),
    ("اللحوم", "كرشة متبلة", [240], "goods"),
    ("اللحوم", "كرشة كيلو متنظفة مع فشة", [200], "goods"),
    ("اللحوم", "العكاوي", [300], "goods"),
    ("اللحوم", "فشة", [170], "goods"),
    ("اللحوم", "باكا تاتا", [450], "goods"),
    ("اللحوم", "وش فخذة", [450], "goods"),
    ("اللحوم", "كولاتة 10% دسم", [430], "goods"),
    ("اللحوم", "كولاتة احمر حافي", [450], "goods"),
    ("اللحوم", "سمائة", [450], "goods"),
    ("اللحوم", "قزموط جرمة لوج احمر", [450], "goods"),
    ("اللحوم", "جباعي وش فخذة", [440], "goods"),
    ("مصنعات وقطعات", "ريش على الجل", [430], "goods"),
    ("مصنعات وقطعات", "كفتة حاتي", [420], "goods"),
    ("مصنعات وقطعات", "كفتة داود باشا", [420], "goods"),
    ("مصنعات وقطعات", "كباب حلة احمر متبل", [500], "goods"),
    ("مصنعات وقطعات", "كفتة ارز", [320], "goods"),
    ("مصنعات وقطعات", "ممبار متبل خفيف", [250], "goods"),
    ("مصنعات وقطعات", "لحمة راس", [250], "goods"),
    ("مصنعات وقطعات", "دهم ضاني", [350], "goods"),
    ("مصنعات وقطعات", "كفتة طرب", [400], "goods"),
    ("مصنعات وقطعات", "شرائح شاورما سادة او متبلة", [450], "goods"),
    ("مصنعات وقطعات", "التوماموك", [400], "goods"),
    ("مصنعات وقطعات", "قطعية ورقة لحمة عجالي", [430, 450], "goods"),
    ("مصنعات وقطعات", "فاهيتا لحمة متبلة", [420], "goods"),
    ("مصنعات وقطعات", "ريب اي ضاني", [500], "goods"),
    ("مصنعات وقطعات", "بوفتيك متبل جاهز / سكالوب بانيه", [500], "goods"),
    ("مصنعات وقطعات", "استيك متبل جاهز", [460], "goods"),
    ("مصنعات وقطعات", "كبدة بلدي متبلة وردة", [500], "goods"),
    ("مصنعات وقطعات", "كبدة متبلة اسكندراني", [480], "goods"),
    ("مصنعات وقطعات", "سبع وسة لحمة", [300], "goods"),
    ("مصنعات وقطعات", "برجر بلدي", [280, 350, 450], "goods"),
    ("مصنعات وقطعات", "سجق بلدي", [280, 350, 450], "goods"),
    ("مصنعات وقطعات", "ممبار محشي رز", [250], "goods"),
    ("مصنعات وقطعات", "ممبار محشي فريك ولحمة", [300], "goods"),
    ("الغازي والحلويات", "لحم ضاني", [500], "goods"),
    ("الغازي والحلويات", "كبدة ضاني", [500], "goods"),
    ("الغازي والحلويات", "لحم ضاني جديان", [500], "goods"),
    ("الغازي والحلويات", "كبدة خالي جديان", [500], "goods"),
    ("الغازي والحلويات", "لحم ضاني خرفان", [470], "goods"),
    ("الغازي والحلويات", "منديل ضاني", [350], "goods"),
    ("الغازي والحلويات", "اللسان", [270], "goods"),
    ("الغازي والحلويات", "المخ", [300], "goods"),
    ("الغازي والحلويات", "الطحال", [150], "goods"),
    ("الغازي والحلويات", "حلويات العجل", [300], "goods"),
    ("برجر ميكس", "برجر لحمة مخصوص بالجبنة الرومي ٦٠٠ جم", [230], "goods"),
    ("برجر ميكس", "برجر لحمة مخصوص بالشيدر", [250], "goods"),
    ("برجر ميكس", "برجر لحمة مخصوص فلامنك", [270], "goods"),
    ("برجر ميكس", "برجر لحمة مخصوص تركي مدخن", [250], "goods"),
    ("برجر ميكس", "برجر لحمة مخصوص موتزاريلا", [220], "goods"),
    ("برجر ميكس", "برجر فراخ بالجبنة رومي ٦٠٠ جم", [220], "goods"),
    ("برجر ميكس", "برجر فراخ فلامنك ٦٠٠ جم", [260], "goods"),
    ("برجر ميكس", "برجر فراخ شيدر ٦٠٠ جم", [240], "goods"),
    ("برجر ميكس", "برجر فراخ موتزاريلا ٦٠٠ جم", [210], "goods"),
    ("برجر ميكس", "برجر فراخ تركي مدخن ٦٠٠ جم", [250], "goods"),
    ("الفراخ", "شيش طاووق", [320], "goods"),
    ("الفراخ", "بانيه متبل", [320], "goods"),
    ("الفراخ", "برجر فراخ", [320], "goods"),
    ("الفراخ", "شاورما فراخ متبلة", [320], "goods"),
    ("الفراخ", "سجق فراخ", [320], "goods"),
    ("الفراخ", "استربس", [320], "goods"),
    ("الفراخ", "كوردن بلو", [320], "goods"),
    ("الفراخ", "سبع وسة فراخ", [280], "goods"),
    ("الفراخ", "سبع وسة جبنة", [250], "goods"),
    ("الفراخ", "كفتة فراخ", [320], "goods"),
    ("خدمات تصنيع وتجهيز اللحوم", "فرم اللحمة", [20], "service"),
    ("خدمات تصنيع وتجهيز اللحوم", "سجق - أقل كمية 2 كيلو", [100], "service"),
    ("خدمات تصنيع وتجهيز اللحوم", "برجر", [80], "service"),
    ("خدمات تصنيع وتجهيز اللحوم", "كفتة حاتي", [50], "service"),
    ("خدمات تصنيع وتجهيز اللحوم", "كفتة داود باشا", [50], "service"),
    ("خدمات تصنيع وتجهيز اللحوم", "بوفتيك تقطيع ودق", [50], "service"),
    ("خدمات تصنيع وتجهيز اللحوم", "بوفتيك متبل ومدقوق", [110], "service"),
    ("خدمات تصنيع وتجهيز اللحوم", "استيك", [40], "service"),
    ("خدمات تصنيع وتجهيز اللحوم", "استيك متبل", [70], "service"),
    ("خدمات تصنيع وتجهيز اللحوم", "شاورما لحم متبلة", [70], "service"),
    ("خدمات تصنيع وتجهيز اللحوم", "شاورما لحم سادة (تقطيع)", [40], "service"),
    ("خدمات تصنيع وتجهيز اللحوم", "تكسير كوارع", [40], "service"),
    ("خدمات تصنيع وتجهيز اللحوم", "تقطيع باكا تاتا", [40], "service"),
]

CAT_PREFIX = {
    "اللحوم": "MEAT", "مصنعات وقطعات": "PREP", "الغازي والحلويات": "LAMB",
    "برجر ميكس": "BRGR", "الفراخ": "CHK", "خدمات تصنيع وتجهيز اللحوم": "SVC",
}


def build_rows() -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    skipped: list[str] = []
    counters: dict[str, int] = {}
    for category, name, prices, kind in MENU:
        counters[category] = counters.get(category, 0) + 1
        if not prices:
            skipped.append(f"{category} / {name} (no price)")
            continue
        unit = "kg" if category == "اللحوم" else "piece"
        for i, price in enumerate(prices, 1):
            seq = counters[category]
            suffix = f" (سعر {i})" if len(prices) > 1 else ""
            sku = f"{CAT_PREFIX[category]}-{seq:02d}{'' if len(prices) == 1 else '-' + str(i)}"
            rows.append({
                "sku": sku, "name": name + suffix, "unit": unit,
                "price": float(price), "stock_quantity": 0,
                "category": category, "kind": kind,
            })
    return rows, skipped


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url")
    ap.add_argument("--business-slug", default="beit-el-lahma")
    ap.add_argument("--commit", action="store_true")
    args = ap.parse_args()

    rows, skipped = build_rows()
    print(f"WOULD CREATE {len(rows)} products "
          f"({sum(1 for r in rows if r['kind'] == 'service')} services), "
          f"SKIP {len(skipped)}:")
    for s in skipped:
        print(f"  SKIP: {s}")
    for r in rows:
        print(f"  {r['sku']:9s} | {r['name']} | {r['unit']:5s} | {r['price']:7.2f} | {r['category']}")

    if not args.commit:
        print("\nDRY RUN - nothing written.")
        return 0
    if not args.database_url:
        print("--commit needs --database-url", file=sys.stderr)
        return 2

    import uuid

    from sqlalchemy import create_engine, text

    engine = create_engine(args.database_url)
    with engine.begin() as conn:
        biz = conn.execute(
            text("SELECT id FROM businesses WHERE slug = :s"), {"s": args.business_slug}
        ).scalar_one_or_none()
        if biz is None:
            print(f"business slug {args.business_slug!r} not found", file=sys.stderr)
            return 2
        existing = conn.execute(
            text("SELECT sku FROM products WHERE business_id = :b"), {"b": biz}
        ).scalars().all()
        if existing:
            print(f"ABORT: products table already has {len(existing)} SKUs for this "
                  f"business; refusing to double-import.", file=sys.stderr)
            return 2
        for r in rows:
            conn.execute(
                text("INSERT INTO products (id, business_id, sku, name, unit, price,"
                     " stock_quantity, is_active) VALUES (:id, :b, :sku, :name, :unit,"
                     " :price, 0, true)"),
                {"id": uuid.uuid4(), "b": biz, "sku": r["sku"], "name": r["name"],
                 "unit": r["unit"], "price": r["price"]},
            )
    print(f"COMMITTED {len(rows)} products to business {args.business_slug}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
