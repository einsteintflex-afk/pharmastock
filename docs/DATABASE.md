# PharmaStock Database

PostgreSQL 16+ (development used 16; the original installation runs 18). The schema is
created and upgraded only by migrations (`backend/migrations`, see MIGRATIONS.md).

## Tables

| Table | Tenant (RLS) | Purpose |
|---|---|---|
| `organizations` | — | Tenants: name, type (community pharmacy, chain, hospital, wholesale), status, plan, limit overrides, billing reference |
| `users` | explicit filter | Accounts: role, organization, optional assigned location, notification preferences, lockout |
| `sessions` | explicit filter | Server-side sessions (SHA-256 of the token), device name, expiry, revocation |
| `password_reset_tokens` | explicit filter | One-time reset tokens (hash only), expiry, use |
| `medicines` | ✓ | Master data: name, strength, form, generic / brand name, route, manufacturer, GTIN, active flag, reorder level, selling price |
| `batches` | ✓ | Stock per medicine, batch number and location: quantity, expiry, cost, supplier, received / purchase date, hold status, scanned barcode |
| `stock_movements` | ✓ | The stock ledger: every change to every batch, user, reason, link to dispensation line or transfer |
| `locations` | ✓ | Pharmacy, central store, store, cold chain, ward, branch, department (tree) |
| `suppliers` | ✓ | Suppliers and contact details, active flag |
| `purchase_orders`, `purchase_order_items`, `purchase_receipts` | ✓ | Purchasing lifecycle and deliveries (each receipt → batch + RECEIVED movement) |
| `dispensations`, `dispensation_items` | ✓ | Counter transactions (prescription / OTC), lines, prices, payment, voids |
| `transfers`, `transfer_items`, `transfer_allocations` | ✓ | Transfers and requisitions, approved quantities, FEFO batches moved (both movements) |
| `notifications`, `notification_reads` | ✓ | In-app notifications (expiry, stock, purchasing, receiving, transfers) and per-user read state |
| `notification_deliveries` | ✓ | E-mail / SMS outbox with retries |
| `scheduled_reports` | ✓ | Report schedules (report, format, frequency, recipients) |
| `app_settings` | ✓ | Per-organization settings (expiry thresholds, reorder rules, currency, receipt header, transfer approval) |
| `audit_log` | ✓ (NULL org allowed for pre-login events) | Append-only company audit trail |
| `stock_adjustments` | ✓ | Every manual stock change: reason code, previous / change / new quantity, cost, status (pending approval, posted, rejected, cancelled), requester, approver, session, device, request id |
| `stock_counts`, `stock_count_lines` | ✓ | Physical counts per location; each line keeps the recorded quantity at the moment of counting and links to the adjustment it produced |
| `idempotency_keys` | ✓ | Stored responses of critical requests (per organization, user and key) |
| `organization_files` | ✓ | Company logo (re-encoded PNG, bytea) |
| `messaging_providers` | ✓ | Organization's own WhatsApp / SMS / SMTP settings (encrypted JSON) |
| `message_consents` | ✓ | Customer consent per phone number and channel (opted in / out, source) |
| `messaging_credit_ledger` | ✓ | Credits bought, used per message, refunded, granted |
| `receipt_links` | — (token lookup) | Digital-receipt token → organization, sale, expiry |
| `whatsapp_numbers` | — (routing) | WhatsApp phone number id → organization, for webhooks |
| `provider_message_routes` | — (routing) | Provider message id → organization and delivery, for status webhooks |
| `plans` | — (catalogue) | Plans: limits, features, optional prices (managed by MedCart Tech) |
| `subscriptions`, `payments`, `messaging_packages` | — (explicit filter) | Paid periods, payments (unique reference, provider, status), credit packages |
| `platform_settings` | — | MedCart Tech settings (e.g. "Powered by MedCart Tech") |
| `platform_audit` | — (platform only) | Append-only audit of platform administrators' actions |
| `security_events` | — (platform only) | Failed sign-ins / codes, refused access, cross-tenant probes, rate limits |
| `platform_opt_outs` | — | STOP replies on the platform WhatsApp number |
| `mfa_challenges` | — | Pending second sign-in steps (token hash, attempts, expiry) |
| `schema_migrations` | — | Applied migrations and checksums |

`users` also holds the encrypted TOTP secret, hashed recovery codes and the last used
time step; `sessions` records whether a session is privileged (platform) and when it
last proved a second factor.

## Integrity rules enforced by the database

- Quantities never negative; received ≤ ordered; unique batch per medicine + number + location.
- Unique per organization: medicine identity (name + strength + form), GTIN, supplier name,
  location name, order number, dispensation number, transfer number, settings key.
- Enumerations as CHECK constraints: movement types, statuses, roles, plans, hold status.
- `audit_log` and `platform_audit` reject UPDATE and DELETE (triggers); the application
  role also has no UPDATE / DELETE privilege on them or on `security_events`.
- Row level security, **forced** also for the table owner: `organization_id = current_org()`,
  where `current_org()` reads the connection setting `app.organization_id`. With no
  organization set, business tables return no rows and reject inserts.

## Roles in production

| Role | Rights |
|---|---|
| `pharmastock_owner` | Owns the database and schema; used **only** to apply migrations (`MIGRATION_DATABASE_URL`) |
| `pharmastock_app` | Used by the application: SELECT / INSERT / UPDATE / DELETE on tables (INSERT / SELECT only on the audit trails), sequences; **not** owner, superuser or BYPASSRLS; cannot change the schema. Privileges are (re)granted by `python -m backend.migrate` when `APP_DB_ROLE` is set. The server refuses a superuser / BYPASSRLS role in production. |
| `pharmastock_backup` | Read-only (`pg_read_all_data`) + BYPASSRLS, for `pg_dump` of all organizations |
| superuser | Only for creating roles and restoring backups |

`deploy/postgres-init.sh` (Docker) or `deploy/create_app_role.sql` (existing server) create them.

## The stock ledger

```
batches.quantity = Σ quantity of RECEIVED, RETURNED, TRANSFER_IN
                 + Σ signed ADJUSTMENT
                 − Σ quantity of DISPENSED, DAMAGED, EXPIRED, TRANSFER_OUT
```

`GET /stock-reconciliation` lists any batch where this does not hold (e.g. after a manual
database edit); `POST /stock-reconciliation/{batch}/resolve` fixes it with an audit entry.
Historical figures (stock trends, stock-out days, turnover) are reconstructed from this ledger.

## Indexes (performance)

Every tenant table has an `organization_id` index; additionally FEFO
(`organization_id, medicine_id, expiry_date` where quantity > 0), movements by batch and
date and by type and date, audit by entity and time, notifications by dedupe key, open
transfers, pending deliveries and due schedules. List endpoints page in SQL
(`COUNT(*) OVER ()`), and detail pages load related rows with set-based queries (no N+1).
