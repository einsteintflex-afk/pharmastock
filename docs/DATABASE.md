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
| `audit_log` | ✓ (NULL org allowed for pre-login events) | Append-only audit trail |
| `schema_migrations` | — | Applied migrations and checksums |

## Integrity rules enforced by the database

- Quantities never negative; received ≤ ordered; unique batch per medicine + number + location.
- Unique per organization: medicine identity (name + strength + form), GTIN, supplier name,
  location name, order number, dispensation number, transfer number, settings key.
- Enumerations as CHECK constraints: movement types, statuses, roles, plans, hold status.
- `audit_log` rejects UPDATE and DELETE (trigger).
- Row level security, **forced** also for the table owner: `organization_id = current_org()`,
  where `current_org()` reads the connection setting `app.organization_id`. With no
  organization set, business tables return no rows and reject inserts.

## Roles in production

| Role | Rights |
|---|---|
| `pharmastock_app` | Owns the schema; used by the application; **not** superuser, **no** BYPASSRLS (so isolation applies). The server refuses to start in production otherwise. |
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
