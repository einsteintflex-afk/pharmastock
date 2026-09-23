# PharmaStock 2.0 — Full Product Delivery Report

Branch `claude/bold-goldberg-p2dvu3` (based on `local-import`; nothing merged into `main`, no pull request).
All figures below were produced by running the code in this environment on 23 Sept 2026.

**Result in one line:** the working 2.0 system was extended (not rebuilt) into a
multi-organization, hospital-ready, mobile-ready platform with 12 migrations that keep
every existing record, 116 permission-checked API endpoints, 221 passing backend tests
and a 65-check browser test with 0 browser errors.

Detailed guides: [Architecture](ARCHITECTURE.md) · [API](API.md) · [Database](DATABASE.md) ·
[Migrations](MIGRATIONS.md) · [Deployment](DEPLOYMENT.md) · [Backup](BACKUP.md) ·
[Security](SECURITY.md) · [Admin guide](ADMIN_GUIDE.md) · [User guide](USER_GUIDE.md) ·
[Troubleshooting](TROUBLESHOOTING.md) · [Mobile](MOBILE.md) · [Limitations](LIMITATIONS.md)

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

The scope document was then audited against the 2.0 branch; everything missing is listed
per section below as "added in this delivery".

---

## 2. Architecture

FastAPI (stateless, uvicorn workers) + PostgreSQL 16+ with row level security; plain
ES-module JavaScript web app served at `/app` (installable PWA); background loops for
notification refresh, the e-mail/SMS outbox and scheduled reports (safe in every process:
`FOR UPDATE SKIP LOCKED`); Caddy/nginx for HTTPS. Routers → services → database; the same
service functions feed the API, reports, background jobs and the AI assistant, so every
screen, export and AI answer shows identical numbers. See ARCHITECTURE.md.

## 3. Database

24 tables, 65 foreign keys, CHECK constraints for every enumeration and quantity rule,
per-organization unique keys, indexes for FEFO, ledger, audit and queues.
**Row level security is enabled and forced on 19 business tables** (verified in
`pg_class`); `users`, `sessions`, `password_reset_tokens` (needed before sign-in) filter by
organization explicitly. See DATABASE.md.

## 4. Migrations and data preservation

12 versioned migrations with checksums; the server refuses to start while any is pending.
Added in this delivery: 0008 tenancy, 0009 master data / holds, 0010 transfers,
0011 outbox / schedules, 0012 password reset / devices.

Verified on the original dumps (`db_schema.sql` + `db_testdata.sql`) → all 12 migrations:

| Table | Before | After |
|---|---|---|
| medicines | 3 | 3 |
| batches | 6 | 6 |
| suppliers | 1 | 1 |
| purchase_orders / items / receipts | 1 / 1 / 1 | 1 / 1 / 1 |
| stock_movements | 2 | 7 (+5 opening-balance movements from 0005) |
| total units in stock | 470 | 470 |
| batches not matching the ledger | — | **0** |

No table was dropped or recreated; existing rows were assigned to organization 1
(Enterprise plan).

## 5. Backend

116 API endpoints (catalogue generated from the code: API.md); only sign-in, forgot
password and reset password are public. Versioned alias `/api/v1`, `limit`/`offset` +
`X-Total-Count`, structured JSON errors, request ids. New services in this delivery:
`trends`, `transfers`, `barcode`, `delivery`, `scheduler`, `organizations`, `plans`.

## 6. Frontend

PharmaStock 2.0 logo on sign-in, brand mark and generated icons; collapsible sidebar,
phone/tablet drawer, tables scroll inside cards, toasts sized for phones (65-check E2E
includes "no horizontal page scroll" at 390 px on four pages). New pages: barcode scan,
transfers / requisitions (list, create, detail with approve / reject / cancel / dispatch /
receive), stock reconciliation, organization & plan, platform administration, e-mail /
SMS outbox and scheduled reports, forgot / reset password; extended: medicines (master
data, active filter, barcode search), batches (hold actions), counter and receiving (scan),
suppliers (receipts, activity), analytics (trends with SVG charts, locations, stock-outs,
suppliers, forecast sufficiency), dashboard (purchases, supplier activity, transfers,
held stock), account (notification preferences, devices), users (location, reset links,
devices), audit and movements (server paging). Screenshots: `docs/screenshots/`.
No duplicate functions, no syntax errors, every import resolves (checked by script, also in CI).

## 7. Security

scrypt passwords, hashed session and reset tokens, lockout, per-IP and per-session rate
limits, CSRF header + origin check, SameSite=Strict/HttpOnly/Secure cookies, strict CSP (no
inline script/style), full security headers, HSTS, forced RLS tenant isolation with a
production check that refuses superuser/BYPASSRLS database roles, append-only audit,
CSV-injection guard, no secrets in code or image. Details and checklist: SECURITY.md.

## 8. Users, roles and authentication

Six roles (Administrator, Manager, Pharmacist, Pharmacy Technician, Storekeeper, Viewer)
mapped to 22 permissions, enforced on the API (23 permission tests). Sign-in / sign-out,
password change, forced change of temporary passwords, lockout, self-service reset by
e-mail (no account enumeration), administrator reset links, named devices with remote
sign-out, session invalidation on password change / reset / deactivation / suspension,
location-scoped staff, platform administrators.

## 9. Inventory, expiry and FEFO

- Medicine master: name, strength, form, generic, brand, route, manufacturer, GTIN
  (check digit validated, unique per organization), active flag (inactive = cannot be
  ordered or stocked), reorder level, price.
- Batches: status (ACTIVE / QUARANTINED / RECALLED; releasing a recall needs a manager),
  purchase and received dates, supplier, cost, scanned barcode, running-balance history.
- Configurable expiry engine (critical / urgent / approaching days) driving statuses,
  alerts, notifications, reports and risk.
- FEFO: usable = ACTIVE and not expired; earliest expiry first with row locks; overrides
  need a permission and a reason; used by the counter, quick dispense, transfers and AI.
- Movements: RECEIVED, DISPENSED, RETURNED, DAMAGED, EXPIRED, ADJUSTMENT (signed),
  TRANSFER_OUT, TRANSFER_IN — each audited; reconciliation report plus resolve action
  (trust ledger / trust count with an ADJUSTMENT), audited.

## 10. Purchasing and suppliers

Purchase orders (draft → ordered → partially received → received / cancelled), lines,
partial receipts that create or top up batches with weighted cost, purchase date and
scanned barcode, notifications. Suppliers: activation, purchase history, products,
**receipt history and activity trail**, performance analytics (spend share, fill rate,
lead time, price changes, last order / delivery).

## 11. Analytics and forecasting

Reorder recommendations with the calculation and projected stock-out date; expiry risk;
consumption and trends; **monthly stock trend** (in / out / write-offs / closing stock
reconstructed from the ledger); **expiry trend** (write-offs by month and 12-month expiry
calendar); **stock-out history** (days and episodes without usable stock, reconstructed per
batch); **location analytics**; supplier performance; purchasing trends; turnover;
valuation. Forecasts report weeks of history, active weeks, variability, **data
sufficiency** (INSUFFICIENT / LIMITED / ADEQUATE), confidence, notes and explicit
limitations. 22 reports in JSON / CSV / Excel / PDF (all four formats tested for every
report); report views and exports are audited; the audit report needs `audit.read`.

## 12. AI assistant

19 read-only tools over the same services (new: monthly summary, stock changes, top
suppliers, overstock, expired stock, stock-outs, locations, open transfers; forecast now
includes sufficiency). Claude (tool loop with server-side fallback) when a key is set;
otherwise a deterministic engine that answers the same questions from the same data.
Every question is audited. The system prompt forbids inventing figures, treats tool
output as data, and states it is inventory decision support, not clinical advice.
Tested questions include "summary of this month", "how has stock changed this month",
"top suppliers", "high stock but low consumption", "value of expired stock", "stock-outs".

## 13. Notifications and delivery

In-app notifications (expiry, low stock, purchasing, receiving, transfers — actionable ones
stay open until handled). E-mail and SMS: per-user opt-in and minimum severity, outbox in
the same transaction, background sending with exponential back-off, SKIPPED with reason
when a channel is not configured, admin list / test / retry. Scheduled reports: daily /
weekly / monthly, PDF / Excel / CSV attachments, rolling periods. Verified against a real
local SMTP server (attachment checked) and an HTTP SMS gateway.

## 14. Barcode

GS1 parser: GTIN-8/12/13/14 with check digits, DataMatrix / GS1-128 element strings with
the GS separator (and `<GS>`, `^]`, symbology prefixes), human-readable `(01)…(17)…(10)…`,
AIs 01, 02, 10, 11, 13, 15, 17, 20, 21, 30, 240, 241; day 00 = end of month; codes
without batch / expiry are accepted. Lookup returns the medicine, matching batch, FEFO
batch, open order lines and warnings (expired pack, expiry mismatch, held batch, FEFO).
Used on the scan page, the dispensing counter, batch registration and purchase receiving.

## 15. Mobile readiness

Responsive installable PWA (manifest, icons, shell-only service worker), camera scanning
where supported, `/api/v1`, bearer tokens with device names and remote revocation,
pagination headers, compact scan endpoint. Native app architecture and the server work it
needs: MOBILE.md.

## 16. Hospital readiness

Organization types (community pharmacy, chain, hospital, wholesale); hospitals start with a
Central Store; location types central store / store / cold chain / ward / department /
branch in a hierarchy; **requisitions** from wards with approval (quantities can be reduced,
optional separate approver), FEFO dispatch, receipt into ward batches with the same number
and expiry, full traceability source batch → destination batch; staff assigned to a ward
are limited to it; hospitals see "Requisitions" in the menu.

## 17. Multi-organization / SaaS readiness

Organizations with status (trial, active, suspended, cancelled) and plan (Basic,
Professional, Enterprise) defining limits (users, locations) and features
(multi-location, advanced analytics, AI assistant, scheduled reports, hospital, API
access) — **no prices in code**; per-organization overrides; billing customer reference
field; platform administration UI and CLI; suspension blocks sign-in and revokes sessions.
Isolation proven by tests: a second organization sees none of organization 1's medicines,
batches, users, audit entries or notifications; org 1 IDs return 404; raw SQL under org 2's
context sees 0 org-1 rows and cannot insert into org 1; with no context nothing is visible.

## 18. Deployment and operations

Dockerfile (non-root, health check, no OS packages), docker-compose (PostgreSQL with
application and backup roles, app, Caddy automatic HTTPS, scheduled backups), staging and
production env templates, nginx alternative with rate limits, GitHub Actions CI.
Observability: `/health`, `/ready` (migration status), `/metrics` (Prometheus, token),
JSON logs with request ids, error webhook / Sentry hook.
**Verified here**: the compose stack started in production mode as a non-superuser role;
HTTP redirected to HTTPS; HSTS, CSP and Secure cookie present; `/docs` 404; metrics 401
without token; sign-in worked; the backup service produced a dump that
`verify_backup.sh` restored (12 migrations, row counts, ledger reconciled). Backup as the
application role fails by design (RLS) — documented. Restore refuses to overwrite a
database and refuses a corrupted file (checksum).

## 19. Testing (actual results)

**Backend — `pytest`: 221 passed, 0 failed (14.0 s)** against a real PostgreSQL database
built from the original dumps + all migrations, as a non-superuser role (RLS enforced):

| File | Tests | Covers |
|---|---|---|
| test_analytics_reports | 33 | consumption, reorder, risk, valuation, forecast, dashboard, every report × 4 formats |
| test_permissions | 23 | role matrix on the API |
| test_assistant | 22 | tools, built-in answers (14 sample questions), Claude loop (mocked), fallback, audit |
| test_inventory_fefo | 17 | expiry statuses, FEFO, overrides, movements, ledger |
| test_master_data | 12 | GS1 parsing, GTIN, master fields, holds, reconciliation actions, pagination, supplier receipts, barcode lookup |
| test_auth, test_legacy_api, test_purchasing, test_hardening, test_trends | 11 each | sign-in / lockout; original API contract; purchasing; rate limit, CSRF, origin, /api/v1, ready, metrics, logs, error hook, devices, password reset; trends, stock-outs, locations, suppliers, forecast sufficiency, AI tools |
| test_delivery | 10 | preferences, queueing by severity, retry / fail, skip, scheduled reports, real SMTP, SMS webhook |
| test_dispensing | 10 | counter, prescriptions, receipts, voids |
| test_audit_notifications | 9 | append-only audit, notifications |
| test_tenancy | 8 | isolation (API, SQL, cross-tenant ids), plan limits and features, platform admin, suspension |
| test_transfers | 8 | full requisition lifecycle, FEFO dispatch, merge, insufficient stock, reject / cancel, validation, location scoping, separate approver, listing |
| test_errors_security | 8 | error handling, headers, tokens |
| test_migrations | 6 | upgrade path and data preservation |

**Browser — Playwright end-to-end: 65/65 checks passed, 0 browser (console / page) errors**
on a fresh copy of the original data: sign-in and forced password change, all 21 pages,
navigation, search / sort, medicine CRUD with validation, dispensing with FEFO and receipt,
void, purchasing and partial receipt, write-off, analytics (11 tabs incl. charts),
reports in 3 formats, AI assistant, notifications, audit, settings, users, GTIN + GS1
scan, scan at the counter, quarantine / release, a full ward requisition, reconciliation,
account preferences and devices, sidebar collapse, service worker, viewer restrictions,
CSRF refusal, mobile drawer and no horizontal scroll, forgot password.

**Static checks**: `compileall` clean; `node --check` on every JS module; no duplicate
function declarations; every named import resolves.

## 20. Known limitations

See LIMITATIONS.md — notably: tested on PostgreSQL 16 / Linux (not yet on your PG18 /
Windows machine), Claude path tested with a mock, no provider-specific SMS adapter,
transfers received all-or-nothing, no billing-provider integration, CI not yet run on
GitHub, camera scanning depends on browser support.

## 21. Remaining work

1. Upgrade a copy of the production database (PG18, Windows), then production — with backup.
2. Enable GitHub Actions; configure SMTP and an SMS gateway adapter.
3. Partial transfer receipts with discrepancy reasons.
4. Push notifications + refresh tokens → native mobile app.
5. Billing provider integration; seasonality-aware forecasting; hospital clinical modules if required.
