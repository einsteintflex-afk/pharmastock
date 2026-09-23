# PharmaStock 2.0 — Audit, Implementation and Delivery Report

Branch: `claude/bold-goldberg-p2dvu3` (based on `local-import`; nothing merged to `main`).

---

## 1. Audit of the imported project (`local-import`)

The imported code was run unchanged against a restored copy of `db_schema.sql`
+ `db_testdata.sql` before anything was modified.

**Working as described:** all 12 GET endpoints returned 200 with the documented
data (Paracetamol 190, Amoxicillin 280, Ibuprofen 0), purchasing/receiving
flow, dashboard rendering, Dashboard ↔ Medicines navigation, no JavaScript
syntax errors, no duplicate functions.

**Defects found (all reproduced, all fixed):**

| # | Defect | Evidence |
|---|--------|----------|
| 1 | Medicines page showed **0 / NO STOCK for every medicine** | `/stock-alerts` did not return `medicine_id`; the frontend joined on it |
| 2 | `POST /batches` with an invalid date → **500** | reproduced |
| 3 | `GET /medicines` → **500** when a medicine has no strength | `response_model` required `str`, column is nullable |
| 4 | `PUT /medicines` accepted a **negative reorder level**; blank names accepted | reproduced |
| 5 | `ADJUSTMENT` stored the counted quantity, not the change → ledger could not be summed; counting stock to **0 was impossible** | reproduced |
| 6 | Batches created via `POST /batches` had **no movement** → stock ledger did not reconcile | 5 of 6 batches unreconciled |
| 7 | Dispensing from **expired batches** allowed; no FEFO enforcement | reproduced |
| 8 | Stock totals included expired stock; dashboard "Total Medicines" counted only medicines with batches | code review |
| 9 | XSS: medicine fields inserted into HTML unescaped | `renderMedicines`, `editMedicine` |
| 10 | No authentication, authorization, audit trail, connection pooling, or error handling | code review |
| 11 | Backup files served publicly at `/app/app_backup.js` etc.; `requirements.txt` UTF-16; `.gitignore` BOM broke its first rule | reproduced |
| 12 | Navigation restored the dashboard by snapshotting `innerHTML`; other sidebar items did nothing | code review |

---

## 2. Architecture

```
frontend/            index.html, style.css (original design kept + additions)
  js/core.js         escaping html`` templates, API client, tables, forms, toasts
  js/app.js          ONE router (URL hash), navigation built from one route table,
                     session state, permission-aware menu
  js/pages/*.js      dashboard, dispensing counter, medicines, inventory, stock, purchasing, suppliers,
                     analytics, reports, notifications, assistant, admin
backend/
  main.py            app assembly: middleware, security headers, error handlers,
                     startup checks (pending migrations → refuse to start)
  config.py          all configuration from environment (.env)
  database.py        psycopg connection pool; one transaction per request
  migrate.py         versioned SQL migrations (+ CLI)
  security.py        scrypt passwords, hashed session tokens, require(permission)
  permissions.py     roles → permissions (single source of truth)
  audit.py           append-only audit trail, written in the same transaction
  routers/           HTTP layer (one module per area)
  services/          business logic shared by API, reports and AI assistant:
                     expiry engine, inventory queries, stock ledger + FEFO, dispensing,
                     analytics, notifications, reports, exporters, assistant
  manage.py          admin CLI
tests/               145 pytest tests + Playwright end-to-end script (49 checks)
```

Design rules followed:
- Every stock change goes through `services/stock.py`, locks the batch row,
  and writes exactly one movement, so `batches.quantity` always equals the
  ledger (verified by `GET /stock-reconciliation`).
- Every figure (dashboard, reports, AI answers) comes from the same service
  functions — no duplicated calculations, no data hard-coded in the frontend.
- Permissions are checked on the server for every route; the frontend only
  hides controls the user cannot use.

---

## 3. Database changes (migrations — existing data preserved)

`python -m backend.migrate` applies versioned SQL files in
`backend/migrations/`, each in its own transaction, recorded with a checksum in
`schema_migrations`. Each migration checks existing data first and **stops
with a clear message** instead of altering rows it cannot safely convert.

| Migration | Change | Why it was necessary |
|-----------|--------|----------------------|
| 0001_baseline | The original 7 tables. **Stamped, not run**, on an existing database | Fresh installs; history starts from the real schema |
| 0002_integrity_constraints | CHECK constraints (non-negative reorder level/quantity, non-blank names, valid movement types), unique medicine identity (name+strength+form, case-insensitive), unique supplier name, FK indexes | API previously accepted invalid data; FK columns were unindexed |
| 0003_locations_and_batch_details | `locations` table (type: PHARMACY/STORE/COLD_CHAIN/WARD/BRANCH/DEPARTMENT, optional parent); `batches.location_id` (all existing → "Main Pharmacy"), `unit_cost`, `supplier_id`, `received_date`, `created_at`; unique (medicine, batch number, location) | Valuation needs a cost per batch; batches entered outside a PO have no other place for it. Cost/supplier/date **backfilled from purchase receipts where traceable** (AMOX002 → ₵5.50, MedSupply); others left unknown — never guessed |
| 0004_users_sessions_audit | `users` (role CHECK), `sessions` (token SHA-256 only), `audit_log` + trigger making it **append-only** | Authentication, authorization, auditability |
| 0005_stock_ledger | `stock_movements.user_id`; ADJUSTMENT now stores the signed change; **one labelled "Opening balance" ADJUSTMENT per unreconciled batch** (dated before its first movement); sign CHECK | Makes the ledger reconcile for every existing batch. No batch quantity changed |
| 0006_notifications_settings_purchasing | `app_settings` (expiry thresholds 30/90/180 days, slow-moving, lead time, cover days, currency ₵), `notifications`, `notification_reads`, `purchase_orders.created_by`, `purchase_receipts.received_by_user_id` | Configurable thresholds; notification centre; purchasing accountability |
| 0007_dispensing | `dispensations` (one per customer/prescription: type OTC/PRESCRIPTION, optional patient name/phone, prescriber, Rx number, payment method, total, status, void details), `dispensation_items` (medicine, quantity, unit price, directions), `stock_movements.dispensation_item_id`, `medicines.selling_price`, receipt header settings | No existing table records a customer transaction, prescription, price or payment. Linking movements to lines gives batch → patient traceability (recalls) and exact-batch voids |

Not added (existing relationships already suffice): roles/permissions tables
(fixed role set in code), separate inventory table (derived from batches),
a batch-supplier link table (supplier derivable from receipts; stored on the
batch only because non-PO batches need it), transfers (see limitations).

Verified on the real dump: 3 medicines, 6 batches, 1 supplier, 1 PO, 1 receipt
and both original movements unchanged; ledger reconciled for all 6 batches;
re-running migrations is a no-op; a fresh empty database builds the full
schema; editing an applied migration is detected.

---

## 4. API

Original endpoints keep their paths, request bodies and response fields
(new fields are additions). **All endpoints except `/`, `/health`,
`/auth/login` and `/app` now require sign-in.**

Behaviour changes to original endpoints (all deliberate fixes):
- `/stock-alerts`: adds `medicine_id`; `current_stock` is **usable** (non-expired) stock; status adds `OUT OF STOCK`.
- `/inventory`: statuses now EXPIRED / **CRITICAL** (≤30 d) / URGENT (≤90 d) / APPROACHING EXPIRY (≤180 d) / NORMAL, thresholds configurable (previously URGENT ≤30, APPROACHING ≤90, hard-coded).
- `POST /stock-movements`: permission per movement type; DISPENSED refuses expired batches and enforces FEFO (409 with the FEFO batch, or `fefo_override_reason` with permission); EXPIRED only for expired batches; ADJUSTMENT/DAMAGED need a reason; counted quantity 0 allowed.
- `POST /batches`: records the initial quantity as a RECEIVED movement; duplicate batch at the same location → 409.
- `POST /purchase-receipts`: rejects already-expired stock; records cost, supplier, location, user; weighted average cost when adding to an existing batch.
- Validation errors → 422 with field messages; database constraint errors → 400/409; unexpected errors → 500 with a request id only.

| Area | Routes |
|------|--------|
| Auth | `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`, `POST /auth/change-password` |
| Users | `GET/POST /users`, `PUT /users/{id}`, `POST /users/{id}/reset-password`, `GET /roles` |
| Medicines | `GET/POST /medicines` (search, sort), `GET/PUT /medicines/{id}` (detail: stock, FEFO order, batches, movements, purchases, risk) |
| Inventory | `GET /inventory` (search/status/location/supplier filters), `GET /expiry-alerts`, `GET /stock-alerts`, `POST /batches`, `GET/PUT /batches/{id}` (history with running balance), `GET /stock-reconciliation` |
| FEFO | `GET /fefo/{medicine_id}?quantity=` (plan), `POST /dispense` (single medicine, allocates across batches) |
| Dispensing counter | `POST /dispensations` (multi-medicine, all-or-nothing, FEFO per line), `GET /dispensations` (date/status/search), `GET /dispensations/summary` (day totals by payment method), `GET /dispensations/{id}`, `POST /dispensations/{id}/void` (returns stock to the same batches) |
| Movements | `GET/POST /stock-movements` (filters), `GET /stock-movements/types` |
| Suppliers | `GET/POST /suppliers`, `GET/PUT /suppliers/{id}`, `POST /suppliers/{id}/activate`, `/deactivate` |
| Purchasing | `GET/POST /purchase-orders`, `GET /purchase-orders/next-number`, `GET/PUT /purchase-orders/{id}`, `POST /purchase-orders/{id}/cancel`, `GET/POST /purchase-orders/{id}/items`, `PUT/DELETE /purchase-orders/{id}/items/{item_id}`, `GET/POST /purchase-receipts` |
| Analytics | `GET /dashboard`, `/consumption`, `/slow-moving-products`, `/analytics/{consumption,reorder,expiry-risk,valuation,forecast,turnover,purchasing}` |
| Reports | `GET /reports`, `GET /reports/{key}?format=json|csv|xlsx|pdf` — inventory, expiry, expired, low-stock, stock-movements, purchases, suppliers, valuation, expiry-loss, consumption, expiry-risk |
| Notifications | `GET /notifications`, `/notifications/unread-count`, `POST /notifications/{id}/read`, `/read-all`, `/refresh` |
| Admin | `GET /audit-log`, `GET/PUT /settings`, `GET/POST /locations`, `PUT /locations/{id}`, `GET /system/migrations` |
| AI | `GET /assistant/status`, `POST /assistant/ask` |
| System | `GET /`, `GET /health`, `/app` |

### Roles

| Permission | Viewer | Storekeeper | Technician | Pharmacist | Manager | Admin |
|---|---|---|---|---|---|---|
| View inventory, analytics, notifications, AI assistant | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Export reports | | ✓ | ✓ | ✓ | ✓ | ✓ |
| Register batches / receive outside PO | | ✓ | | ✓ | ✓ | ✓ |
| Returns, damage, expiry write-off, stock-count adjustment | | ✓ | | ✓ | ✓ | ✓ |
| Receive purchase deliveries | | ✓ | ✓ | ✓ | ✓ | ✓ |
| Dispense (FEFO) | | | ✓ | ✓ | ✓ | ✓ |
| Override FEFO (with reason) | | | | ✓ | ✓ | ✓ |
| Void a dispensation | | | | ✓ | ✓ | ✓ |
| Edit medicines, suppliers; create/cancel POs | | | | ✓ | ✓ | ✓ |
| Locations, settings, audit trail | | | | | ✓ | ✓ |
| Users | | | | | | ✓ |

---

## 5. Intelligence — how figures are computed

- **Expiry status**: `days = expiry_date − today`; thresholds from Settings. A batch is usable on its expiry date.
- **FEFO**: usable batches (quantity > 0, not expired) ordered by expiry date, then id; dispensing locks them (`SELECT … FOR UPDATE`) and allocates earliest first, splitting across batches. Insufficient stock → nothing changes.
- **Consumption**: DISPENSED units; daily rate = last 30 days ÷ 30 (or 90-day ÷ 90 if none recently); trend = last 30 vs previous 30 days (±20%).
- **Reorder**: recommended when usable ≤ reorder level or days of stock ≤ lead time; quantity = max(reorder level, daily × (lead + cover days)) − usable − on order.
- **Expiry risk**: each medicine's batches consumed in FEFO order at the daily rate; units left at a batch's expiry are "at risk", valued at batch cost. HIGH = at risk within the urgent window, or ≥ 50 % at risk within the approaching window; MEDIUM = at risk further out (dead-stock risk).
- **Valuation**: quantity × batch unit cost; units without a recorded cost are counted and reported, never valued at a guessed price.
- **Forecast**: exponential smoothing (α 0.5) of 12 weekly dispensing totals; confidence from weeks with activity.
- **Turnover**: 90-day dispensed ÷ average of current and 90-days-ago stock (reconstructed from the ledger).
- **AI assistant**: with `ANTHROPIC_API_KEY`, Claude answers using 11 read-only tools over these same functions (tool results are treated as data, not instructions); without a key, a built-in engine routes the question to the same tools. Every query is audited.

---

## 6. Frontend

One application shell (`js/app.js`) with a single route table driving both
the router and the sidebar; URL hash routes (bookmarkable, Back button works);
each page renders into a fresh element so no event handlers leak between pages.
All text is inserted through an escaping template function; no inline
JavaScript (enforced by a Content-Security-Policy of `script-src 'self'`).
The original visual design and CSS were kept and extended.

Pages: Sign-in, Dashboard, Medicines (+ detail), Inventory (+ batch detail),
Expiry Alerts (write-off), **Dispensing Counter** (search → cart with live FEFO
batches per line → prescription/OTC details → payment → printable receipt),
Dispensing History (+ record, reprint, void), Stock Movements,
Purchasing (+ order detail, receiving), Suppliers (+ detail), Analytics (7 tabs),
Reports (run + CSV/Excel/PDF), AI Assistant, Notifications, Audit Trail,
Users (+ role matrix), Settings (+ locations), My Account.

The old `frontend/app.js` and backup files were moved to `archive/` (not served).

---

## 7. Test results (this branch)

| Check | Result |
|---|---|
| Python compile of all backend modules | pass |
| JavaScript syntax check of all 13 modules; duplicate-function scan | pass; none |
| Backend startup (migrations check, bootstrap admin, pool, notification job) | clean |
| `pytest` — 145 tests (incl. 11 for the dispensing counter: multi-line FEFO, all-or-nothing, prices/totals, prescription rules, expired stock never dispensed, permissions, void to same batches, summary, report): migrations & data preservation, auth, lockout, sessions, permissions matrix, legacy API regression, expiry engine, FEFO, ledger reconciliation, movements, purchasing (partial/full/cancel/validation), analytics numbers checked against hand calculations, all 11 reports × 4 formats, CSV-injection, audit append-only, notifications, locations, assistant (built-in + mocked Claude tool loop), error handling, security headers | **145 passed** |
| Playwright end-to-end (Chromium) — 49 checks (incl. a two-medicine prescription at the counter with FEFO, directions, NHIS payment and receipt; history search; stock deduction; void with stock returned and ledger reconciled): sign-in, forced password change, every page, navigation, search, sort, add/edit/validate medicine, FEFO dispensing, write-off, supplier, PO create → receive, analytics, report exports, assistant, notifications, audit, settings validation, user creation, sign-out, viewer restrictions (UI and API), mobile layout | **49/49 passed, 0 browser console/page errors** |

Tests ran on PostgreSQL 16 in a cloud container using your dumps; your server
is PostgreSQL 18 (the SQL used is compatible; the test loader strips the
PG18-only `\restrict` lines).

---

## 8. Known limitations

- **Not yet run on your Windows machine / PostgreSQL 18.** Back up first, run `python -m backend.migrate status`, then migrate.
- The AI assistant's Claude path was verified with a mocked client (no API key in this environment); the built-in engine was tested against real data.
- Stock **transfers between locations** are not implemented (the schema supports them: batches are per location). Dispensing can be limited to a location.
- Forecasting is statistical (exponential smoothing), deliberately simple given the small history; confidence is reported.
- Notifications are in-app only (no email/SMS). The background refresh runs inside the web process (every 15 min) — with several server processes each runs it (harmless, idempotent).
- Login throttling is per account (lockout); there is no per-IP rate limit — add one at the reverse proxy for internet exposure.
- HTTPS must be provided by the deployment (reverse proxy); set `PHARMASTOCK_ENV=production` there.
- Existing batches (except AMOX002) have no recorded unit cost, so their value shows as "units without cost" until entered (Batch → Edit details).
- Dates use the database server's local date (`CURRENT_DATE`).

## 9. Remaining work (suggested order)

1. Run the upgrade on the Windows installation (backup → migrate → smoke test) and enter unit costs for existing batches.
2. Stock transfers between locations (TRANSFER_OUT/IN movements) and requisitions for hospital use.
3. Email/SMS notification delivery; scheduled report emails.
4. Deployment: reverse proxy with HTTPS, automated daily `pg_dump`, CI running pytest + the Playwright script.
5. Barcode scanning (GS1 DataMatrix: product, batch, expiry) for receiving and dispensing.
6. Multi-organisation (tenant) support if offered as SaaS; per-location permissions.
7. Mobile app using the same API (Bearer tokens are already supported).
