# Migration Guide

## How migrations work

- Files `backend/migrations/NNNN_name.sql` are applied in order, each in its own
  transaction, and recorded in `schema_migrations` with a checksum.
- A changed file that was already applied is refused (checksum mismatch).
- `0001_baseline` describes the original schema. On an existing database it is
  **stamped** (not run), so existing data is never touched by it.
- The server refuses to start while migrations are pending; `/ready` reports them.

```
python -m backend.migrate status   # applied and pending
python -m backend.migrate          # apply pending migrations
```

Migrations run with `MIGRATION_DATABASE_URL` (the schema owner) when set, otherwise
`DATABASE_URL`. With `APP_DB_ROLE=pharmastock_app` the run ends by granting the
application role its row privileges on every table (idempotent), so new tables are
covered automatically.

In Docker the entrypoint applies migrations before starting (set `RUN_MIGRATIONS=false`
to do it manually).

## Upgrading an existing installation

1. **Back up** (see BACKUP.md) and keep the file.
2. `git fetch && git checkout <release branch>`; `pip install -r requirements.txt`.
3. `python -m backend.migrate status` — read what will run.
4. `python -m backend.migrate`.
5. Check: `python -m backend.manage reconcile` prints "stock ledger reconciled";
   `/ready` answers `ready`.
6. Start the server.

**Rollback** = stop the server, restore the pre-upgrade backup into the database, check
out the previous release. Migrations are forward-only by design (no down scripts): a
restore is the only rollback that is guaranteed to be complete.

## Migration history

| Version | Change | Data |
|---|---|---|
| 0001 | Baseline (original schema) | stamped on existing DBs |
| 0002 | Integrity constraints (non-negative, uniqueness, enums) | pre-checked; fails loudly if data violates |
| 0003 | Locations; batch cost, supplier, received date | existing batches → "Main Pharmacy" |
| 0004 | Users, sessions, append-only audit log | — |
| 0005 | Stock ledger; opening-balance movements | makes every existing batch reconcile |
| 0006 | Notifications, settings, purchasing extensions | defaults inserted |
| 0007 | Dispensing counter; selling price | — |
| 0008 | Organizations, tenant columns, forced row level security | all existing rows → organization 1 (ENTERPRISE) |
| 0009 | Medicine master data, batch hold status, purchase date, barcode, FEFO indexes | generic name := name; purchase date from receipts |
| 0010 | Transfers and requisitions, TRANSFER_IN/OUT, transfer notifications | setting added per organization |
| 0011 | Notification preferences, delivery outbox, scheduled reports | — |
| 0012 | Password reset tokens, session device names | — |
| 0013 | New roles; MFA fields and challenges; platform audit (append-only); security events; idempotency keys; stock adjustments and counts; purchase approval states and expected date; receipt discount / tax / token / consent; logo files; plans catalogue (organizations.plan becomes a foreign key), subscriptions, payments, credit packages and ledger; messaging providers, consents, routes, opt-outs; MedCart Tech platform settings; new organization settings | additive; existing organizations marked onboarded; new settings inserted for every organization; no existing row changed otherwise |
| 0014 | WhatsApp number → organization routing; index on deliveries by sale | additive |

## Writing a new migration

- Name it with the next number; never edit an applied file.
- Tenant tables: add `organization_id integer NOT NULL DEFAULT current_org()
  REFERENCES organizations(id)`, an index, `ENABLE` + `FORCE ROW LEVEL SECURITY` and the
  `tenant_isolation` policy (see 0010 for the loop).
- Data changes on forced tables must run with the policy lifted for the owner:
  `ALTER TABLE t NO FORCE ROW LEVEL SECURITY; UPDATE …; ALTER TABLE t FORCE ROW LEVEL SECURITY;`
  (see 0009), otherwise the UPDATE silently affects no rows.
- Add a test in `tests/test_migrations.py` if the migration transforms data.
