# Waseet AI

منصة ذكية لأتمتة عمليات تجار الجملة في مصر. هذا المستودع يحتوي على **Milestone 1**:
أساس المشروع + قاعدة بيانات PostgreSQL + API معزول لكل شركة (multi-tenant) + سجل تدقيق.
**من غير LLM لحد دلوقتي.**

## المتطلبات

- Python 3.10+
- PostgreSQL 14+ (مباشرة على الجهاز، أو بالـ Docker عبر `docker-compose.yml`)

## التشغيل خطوة بخطوة

```bash
# 1) بيئة افتراضية وتثبيت المكتبات
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2) قاعدة البيانات
#    لو عندك Docker:  docker compose up -d
#    لو PostgreSQL مثبت مباشرة، أنشئ القاعدة:
createdb waseet_dev

# 3) الإعدادات
cp .env.example .env    # عدّل DATABASE_URL لو لزم

# 4) الجداول + بيانات تجريبية (شركتين)
python -m alembic upgrade head
python -m app.seed

# 5) تشغيل السيرفر
uvicorn app.main:app --reload
```

التوثيق التفاعلي: `http://localhost:8000/docs`

## الاستخدام

كل الطلبات تحتاج هيدر `X-Business-ID` (UUID الشركة). الشركتان التجريبيتان:
`synthetic-foods-co` و `synthetic-plastics-co` — الـ seed بيطبع الـ UUIDs.

```bash
curl -H "X-Business-ID: <uuid>" http://localhost:8000/api/v1/products
curl -X POST -H "X-Business-ID: <uuid>" -H "Content-Type: application/json" \
  -d '{"sku":"RICE-5KG","name":"أرز 5 كيلو","unit":"bag","price":"185.00","stock_quantity":120}' \
  http://localhost:8000/api/v1/products
```

## الاختبارات

```bash
createdb waseet_test
export TEST_DATABASE_URL=postgresql+psycopg2://postgres@localhost:5432/waseet_test
python -m pytest -q
```

الاختبارات بتغطي: صحة السيرفر، عزل الشركات (cross-tenant 404 على الـ API وعلى
مستوى قاعدة البيانات بالمفتاح المركب)، رفض SKU المكرر داخل نفس الشركة وقبوله بين
شركتين، وصعود وهبوط الـ migrations على قاعدة نظيفة.

## البنية

```
app/
  main.py              نقطة دخول FastAPI
  config.py            الإعدادات (pydantic-settings)
  db.py                الاتصال والـ Session
  models.py            businesses / users / products / product_aliases / audit_events
  schemas.py           Pydantic schemas
  audit.py             كاتب سجل التدقيق
  seed.py              بيانات تجريبية
  api/deps.py          حل الـ tenant من X-Business-ID
  api/routes/          health + products
  repositories/        كل الاستعلامات معزولة بـ business_id
alembic/               الهجرات
tests/                 pytest
```

---

## Milestone 2 — العملاء والتسعير والمخزون والطلبات

جداول جديدة (migration `m2_0001`): `customers` / `customer_prices` / `inventory_movements` /
`orders` / `order_items`. كل جدول فيه `business_id` وكل علاقة بين الجداول بمفتاح مركب
`(business_id, id)` فمستحيل قاعدة البيانات تقبل ربط بين شركتين.

```bash
python -m alembic upgrade head
python -m app.seed && python -m app.seed_orders   # عملاء وأسعار خاصة وطلب معلّق لكل شركة
```

دورة الطلب: `draft` ← (submit) ← `pending` ← (confirm | reject) ← `confirmed` | `rejected`.

| الطريق | الوظيفة |
|---|---|
| `POST/GET /api/v1/customers`, `GET/PATCH /customers/{id}` | العملاء (الموبايل فريد داخل الشركة) |
| `PUT/DELETE /customers/{id}/prices/{product_id}`, `GET /customers/{id}/prices` | سعر خاص لعميل |
| `POST /products/{id}/stock` | إضافة/خصم مخزون يدويًا (`delta` + `reason`) |
| `GET /products/{id}/movements`, `GET /inventory/low-stock?threshold=` | سجل حركة المخزون والنواقص |
| `POST /orders` (+ items اختيارية), `GET /orders?status=`, `GET /orders/{id}` | الطلبات |
| `POST /orders/{id}/items`, `DELETE /orders/{id}/items/{item_id}` | تعديل المسودة فقط |
| `POST /orders/{id}/submit` / `confirm` / `reject` | اعتماد الطلب بموافقة المستخدم |
| `DELETE /orders/{id}` | حذف المسودة فقط |

قواعد مهمة: السعر الخاص بالعميل يغلب سعر الكتالوج، والسعر يتجمّد على بند الطلب وقت
الإضافة. المخزون يتخصم عند `confirm` فقط، بقفل صف (`FOR UPDATE`) فلا يحصل بيع زيادة،
وأي بند ناقص يلغي العملية كلها. حقل `channel` + `source_text` في الطلب هو مدخل القنوات
(واتساب/تليجرام/ويب) من غير ما المنطق يعرف القناة.
