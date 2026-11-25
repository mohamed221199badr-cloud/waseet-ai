# Restaurant AI Chatbot: Requirements

Status: consolidated from the project owner's brief (Telegram, 26 Nov 2026) and the repository's recorded
decisions. There is no separate requirements handover file. Original questionnaire items 1-15 are
**Not Yet Recovered** and must not be assumed. Nothing here may be invented: add a requirement only when
the owner confirms it.

Legend: **Confirmed** = stated by the owner. **Assumption** = working choice, open to reversal.
**Unresolved** = decision needed. **Not Yet Recovered** = was said to exist, text unavailable.

The repository began as Waseet AI (wholesale order platform). The owner confirmed it is being repurposed
into the Restaurant AI Chatbot; the repo name and README will change.

## 1. Confirmed requirements

| ID | Requirement | Source |
|----|-------------|--------|
| R-01 | Customers chat with the restaurant through Telegram. | Brief |
| R-02 | Staff and customer notifications are sent through Telegram. | Brief |
| R-03 | The customer experience is Arabic only. | Brief |
| R-04 | Payment methods: cash on delivery and online payment. | Brief |
| R-05 | Single restaurant, single branch. | Brief |
| R-06 | Hybrid AI: deterministic workflows plus AI/NLP, with escalation to a human. | Brief |
| R-07 | The LLM never runs unrestricted database operations. Every critical action goes through validated services. | Brief |
| R-08 | Full admin dashboard. | Brief |
| R-09 | Ingredient-level inventory. | Brief |
| R-10 | Employee coordination is manual (no automated dispatch). | Brief |
| R-11 | CRM and customer segmentation. | Brief |
| R-12 | Six employee roles. | Brief |
| R-13 | Reservations are a future feature, not in the first release. | Brief |
| R-14 | Free-tier-first deployment. No paid dependency without a written reason. A free tier is not assumed to be free forever. | Brief |
| R-15 | Tests required for order lifecycle, payments, authorization, and error/edge cases. No feature is claimed to work until tested. | Brief |
| R-16 | Working rules: do not restart the project, do not repeat the questionnaire, do not change confirmed decisions, preserve working code, work incrementally with checkpoints and tests, report real results. | Brief |
| R-17 | Design for extensibility and growth. | Owner, 26 Nov |

## 2. Decisions recorded in the repository (carry over)

| ID | Decision | Where |
|----|----------|-------|
| D-01 | PostgreSQL, FastAPI, SQLAlchemy 2, Alembic. | PROJECT_LOG.md |
| D-02 | Tenant isolation enforced in code and by composite foreign keys. Kept as defence in depth even for one restaurant. | PROJECT_LOG.md |
| D-03 | JWT authentication, tenant taken from the token only. | PROJECT_LOG.md |
| D-04 | Orders go draft, pending, confirmed or rejected, with stock locking. | PROJECT_LOG.md |
| D-05 | Chat agent: rules and database decide; the LLM is optional. `LLM_MODE=open` is allowed only when `APP_ENV=dev`. | docs/CHAT_AGENT.md, app/config.py |

## 3. Assumptions (open to reversal)

| ID | Assumption |
|----|------------|
| A-01 | Existing wholesale code is the base; the domain model (menu, modifiers, recipes, delivery) is reworked, not rewritten from scratch. |
| A-02 | Free-tier hosting as already prepared (render.yaml, managed PostgreSQL). Limits to be verified before launch. |
| A-03 | The tenant model stays, with one restaurant row. |

## 4. Unresolved decisions

| ID | Decision needed |
|----|-----------------|
| U-01 | The six employee roles and their permissions (owner said they will provide them). |
| U-02 | Online payment provider and supported methods in Egypt. |
| U-03 | LLM provider and hosting (free-tier limits, Arabic quality). |
| U-04 | Delivery rules: zones, fees, minimum order, hours. |
| U-05 | Menu structure: categories, modifiers, sizes, combos. |
| U-06 | Human escalation: who receives it, through what channel, expected response time. |
| U-07 | CRM segment definitions and the data kept per customer. |
| U-08 | Data retention and privacy rules for customer chats and phone numbers. |

## 5. Not Yet Recovered

Questionnaire items 1-15 as originally recorded. Any requirement they contained that is not listed in
section 1 is unknown and stays unknown until the owner restates it.
