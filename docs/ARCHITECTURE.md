# PharmaStock Architecture

## Overview

```
 Browser (web app, PWA)       Mobile app (planned, see MOBILE.md)      Integrations
        │  cookie + CSRF header          │ Bearer token /api/v1              │ Bearer token
        └──────────────┬─────────────────┴───────────────────────────────────┘
                       ▼
            HTTPS reverse proxy (Caddy / nginx): TLS, HSTS, rate limit
                       ▼
            FastAPI application (uvicorn workers, stateless)
              middleware: request id, rate limit, CSRF/origin, security headers, metrics
              routers ──► services (business logic) ──► PostgreSQL (row level security)
              background: notification refresh · messaging outbox · scheduled reports ·
                          subscription expiry flags
            ◄── signed webhooks: WhatsApp Business Platform, Paystack
                       ▼
            PostgreSQL 16+: one database, many organizations (tenants)
```

The application is stateless: sessions, notifications, the outbox and schedules
live in PostgreSQL, so more application processes can be added behind the proxy.
Background jobs are safe to run in every process (row locks with `SKIP LOCKED`).

## Code layout

```
backend/
  main.py            app assembly, middleware, error handlers, /health /ready /metrics,
                     background loops, startup checks (pending migrations, DB role)
  config.py          all configuration from environment variables
  database.py        connection pool; tenant context (app.organization_id) per request
  migrate.py         versioned SQL migrations with checksums (+ CLI)
  security.py        scrypt passwords, hashed session and reset tokens, require(permission),
                     require_feature(feature), require_platform_admin (MFA session),
                     require_step_up, location scoping
  crypto.py          Fernet encryption of stored secrets (SECRET_KEY), TOTP, recovery codes
  idempotency.py     Idempotency-Key handling for critical writes
  security_events.py security monitoring records (own connection)
  permissions.py     roles → permissions (single source of truth)
  audit.py           company audit + platform audit, same transaction, secrets scrubbed
  pagination.py      limit/offset + X-Total-Count
  ratelimit.py       sliding-window limiter
  observability.py   JSON logs with request ids, metrics, error reporting hook
  manage.py          administration CLI
  routers/           HTTP layer: auth (+MFA), users, organizations (+onboarding), platform
                     (MedCart console), billing, branding (logo, receipts, /r/ links),
                     messaging (+webhooks), medicines, barcode, inventory, stock,
                     stock_control (adjustments, counts), dispensing, transfers, suppliers,
                     purchasing (+approval, reorder, price history), analytics (+attention,
                     daily brief, movers, search), reports, delivery, admin, assistant
  services/          business logic used by the API, reports, background jobs and the AI:
                     expiry, inventory, stock (ledger + FEFO), dispensing, transfers,
                     barcode (GS1), analytics, trends, notifications, delivery, scheduler,
                     reports, exporters, assistant, organizations, plans (DB catalogue),
                     app_settings, adjustments, stock_counts, billing (payments, credits),
                     branding (logo, receipt HTML / PDF), messaging (receipts, providers,
                     WhatsApp webhooks), intelligence (attention, brief, movers, search)
  migrations/        0001 … 0014 SQL files
frontend/
  index.html, style.css, manifest.json, sw.js, assets/ (logo, icons)
  js/core.js         escaping html`` templates, API client, tables, pager, forms, toasts
  js/app.js          single router + route table (permissions and plan features)
  js/pages/          one module per area
deploy/              Dockerfile entrypoint, Caddyfile, nginx.conf, PostgreSQL init,
                     backup / restore / verify scripts, environment templates
tests/               pytest suite (real PostgreSQL) + Playwright end-to-end script
```

## Key design rules

1. **One stock ledger.** Every change to `batches.quantity` goes through `services/stock.py`
   (or the dispensing / transfer / purchasing services that call it) inside one
   transaction with the batch row locked, and writes exactly one `stock_movements` row.
   `quantity = Σ(RECEIVED, RETURNED, ADJUSTMENT, TRANSFER_IN) − Σ(DISPENSED, DAMAGED,
   EXPIRED, TRANSFER_OUT)` holds for every batch; `/stock-reconciliation` proves it.
2. **FEFO everywhere.** Dispensing, the dispensing counter, transfers and the AI all use
   `stock.fefo_plan`: usable batches (ACTIVE hold status, not expired) earliest expiry
   first. Picking another batch needs `stock.fefo_override` and a reason.
3. **Permissions on the server.** The frontend hides what a role cannot do, but every
   endpoint checks the permission (and plan feature) itself.
4. **Tenant isolation in the database.** PostgreSQL row level security (FORCE) on every
   business table; the request's organization is set on the connection after
   authentication. Application code cannot forget a filter.
5. **Everything audited.** Changes, sign-ins, exports, report views, AI questions,
   scheduled report runs and administrative actions go to `audit_log` in the same
   transaction; platform administrators' actions go to the separate `platform_audit`.
   Both are append-only.
8. **Manual stock changes are adjustments.** Returns, damage, expiry write-offs,
   count differences and corrections go through `services/adjustments.py` (reason code,
   before / after, approval thresholds), which posts the ledger movement.
9. **Least privilege.** Migrations run as the schema owner; the API runs as a role with
   row privileges only. Platform access needs a second factor; risky actions a fresh one.
10. **Outbox for everything outbound.** E-mail, SMS and WhatsApp are queued in the same
    transaction and sent by the worker; payments and messages only change state through
    verified provider callbacks.
6. **Explainable intelligence.** Reorder, expiry risk, forecasts and stock-out history
   return their method, inputs, data sufficiency and limitations with the numbers.
7. **AI is decision support only.** The assistant reads data through read-only tools;
   it cannot change anything and gives no clinical advice.

## Request lifecycle

1. Middleware assigns a request id, rewrites `/api/v1/*`, applies rate limits and the
   CSRF / origin check.
2. `get_current_user` validates the session token (hash lookup), rejects suspended
   organizations, sets `app.organization_id` on the pooled connection.
3. `require(permission)` / `require_feature(feature)` authorize.
4. The router calls services; the transaction commits once at the end.
5. The pool resets the connection (`RESET ALL`) before reuse, so tenant context never
   leaks between requests.
6. Middleware adds security headers, logs the request and records metrics.
