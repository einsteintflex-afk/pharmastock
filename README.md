# PharmaStock

Pharmacy and healthcare inventory intelligence platform: medicines, batches,
expiry engine, genuine FEFO dispensing, purchasing and receiving, stock
ledger, low-stock and reorder intelligence, expiry-risk analytics, stock
valuation, reports (CSV / Excel / PDF), audit trail, notifications, role-based
access and an AI inventory assistant.

FastAPI + PostgreSQL backend, plain JavaScript (ES modules) frontend served at `/app`.

See **[docs/DELIVERY.md](docs/DELIVERY.md)** for the audit, architecture,
database changes, API, test results and known limitations.

---

## Running on Windows (existing installation)

> **Back up the database first** (see *Backup* below). The upgrade migrations
> keep all existing data, but a backup is always required before a schema change.

```powershell
cd C:\Users\TOMMY\PHARMASTOCK
git fetch origin
git checkout claude/bold-goldberg-p2dvu3

.venv\Scripts\activate
pip install -r requirements.txt
```

Edit `.env` (see `.env.example`). Keep your existing `DATABASE_URL`, and add
a first administrator:

```
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=<at least 10 characters, letters and numbers>
```

Apply the database migrations, then start the server:

```powershell
python -m backend.migrate status     # what will be applied
python -m backend.migrate            # apply
uvicorn backend.main:app --reload
```

Open <http://127.0.0.1:8000/app/>, sign in as the bootstrap administrator and
choose a new password. Then remove the `BOOTSTRAP_ADMIN_*` lines from `.env`
and create accounts for staff under **Users**.

The server refuses to start while migrations are pending, so it can never run
against a half-upgraded database.

## Administration commands

```powershell
python -m backend.manage create-admin          # interactive
python -m backend.manage reset-password        # interactive, any user
python -m backend.manage reconcile             # batches vs stock ledger
python -m backend.manage refresh-notifications
python -m backend.migrate status
```

## Backup and restore

```powershell
# Backup (custom format, includes schema + data)
pg_dump -U postgres -d pharmastock -F c -f backups\pharmastock-YYYYMMDD.dump

# Restore into a NEW database (never over the live one without a backup)
createdb -U postgres pharmastock_restore
pg_restore -U postgres -d pharmastock_restore backups\pharmastock-YYYYMMDD.dump
```

Rolling back the upgrade = restore the pre-upgrade backup and check out the
`local-import` branch.

## Tests

Backend (needs a PostgreSQL role allowed to create databases; it builds a
throw-away database from `db_schema.sql` + `db_testdata.sql` + migrations —
the real database is never touched):

```powershell
pip install -r requirements-dev.txt
$env:TEST_DATABASE_URL="postgresql://postgres:PASSWORD@localhost:5432/pharmastock_test"
pytest
```

Browser end-to-end test (Node + Playwright, against a server running on a
**test copy** of the database — it creates records):

```powershell
npm install playwright
npx playwright install chromium
$env:ADMIN_PASSWORD="<bootstrap password>"; node tests/e2e/ui_e2e.mjs screenshots
```
