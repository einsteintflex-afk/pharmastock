# PharmaStock 2.0

![PharmaStock 2.0](frontend/assets/logo.webp)

*Powered by MedCart Tech.*

Pharmacy and hospital inventory intelligence platform for community pharmacies, chains,
hospitals and wholesalers: dispensing counter (prescription / OTC, receipts, voids),
medicine master data with GS1 barcodes, batches with quarantine / recall, configurable
expiry engine, genuine FEFO, stock ledger with reconciliation, purchasing and suppliers,
transfers and ward requisitions with approval, explainable reorder, expiry-risk,
stock-out and forecasting analytics, 26 reports (CSV / Excel / PDF with company branding,
scheduled by e-mail), notifications (in-app, e-mail, SMS), append-only audit trails,
eleven roles, a command center (what needs attention, daily brief), Scan Center (phone
camera), stock counts and adjustments with approval, purchase approvals, branded receipts
(PDF, thermal, digital link) with customer messages on WhatsApp Business / SMS / e-mail,
two-step verification, multi-organization SaaS with database-enforced isolation, plans,
subscriptions, verified payments and messaging credits, the MedCart Tech platform console,
an AI inventory assistant (decision support only), and a responsive installable web app.

FastAPI + PostgreSQL (row level security) backend; plain JavaScript (ES modules) frontend
served at `/app`; Docker / HTTPS deployment.

## Documentation

| | |
|---|---|
| **[Delivery report](docs/DELIVERY.md)** | What was built, verified results, limitations |
| [Architecture](docs/ARCHITECTURE.md) · [Database](docs/DATABASE.md) · [API](docs/API.md) | How it works |
| [Deployment](docs/DEPLOYMENT.md) · [Migrations](docs/MIGRATIONS.md) · [Backup](docs/BACKUP.md) | Operating it |
| [Security](docs/SECURITY.md) · [Troubleshooting](docs/TROUBLESHOOTING.md) | Keeping it safe and running |
| [Admin guide](docs/ADMIN_GUIDE.md) · [User guide](docs/USER_GUIDE.md) | Using it |
| [Mobile](docs/MOBILE.md) · [Performance](docs/PERFORMANCE.md) · [Limitations & remaining work](docs/LIMITATIONS.md) | What is next |
| [Gap analysis](docs/GAP_ANALYSIS.md) | Audit before the MedCart Tech release |

## Quick start with Docker

```sh
cp deploy/env.production.example .env.production   # fill in the change-me values
docker compose --env-file .env.production up -d --build
```

Then open `https://<DOMAIN>/app/` (see [Deployment](docs/DEPLOYMENT.md)).

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
a first administrator (only if no user exists yet) and the encryption key:

```
BOOTSTRAP_ADMIN_USERNAME=admin
BOOTSTRAP_ADMIN_PASSWORD=<at least 10 characters, letters and numbers>
SECRET_KEY=<python -c "import secrets; print(secrets.token_urlsafe(48))">
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
python -m backend.manage create-organization   # new tenant + first administrator
python -m backend.manage reset-password        # interactive, any user
python -m backend.manage reconcile             # batches vs stock ledger
python -m backend.manage refresh-notifications
python -m backend.manage process-deliveries    # scheduled reports + e-mail / SMS outbox now
python -m backend.migrate status
```

## Backup and restore

```powershell
# Backup as postgres (row level security: the application role cannot dump other organizations)
pg_dump -U postgres -d pharmastock -F c -f backups\pharmastock-YYYYMMDD.dump

# Restore into a NEW database (never over the live one without a backup)
createdb -U postgres pharmastock_restore
pg_restore -U postgres -d pharmastock_restore backups\pharmastock-YYYYMMDD.dump
```

Scripts with checksums, retention and automatic verification: [docs/BACKUP.md](docs/BACKUP.md).

Rolling back the upgrade = restore the pre-upgrade backup and check out the
`local-import` branch.

## Tests

Backend (needs a PostgreSQL role allowed to create databases; it builds a
throw-away database from `db_schema.sql` + `db_testdata.sql` + migrations —
the real database is never touched):

```powershell
pip install -r requirements-dev.txt
# A non-superuser role (a superuser bypasses row level security, so the tenancy tests would fail):
psql -U postgres -c "CREATE ROLE pharmastock_test LOGIN PASSWORD 'test-pass' CREATEDB CREATEROLE"
$env:TEST_DATABASE_URL="postgresql://pharmastock_test:test-pass@localhost:5432/pharmastock_test"
pytest          # 293 tests; the API runs as a restricted role the fixtures create
```

Browser end-to-end test (Node + Playwright, against a server running on a
**test copy** of the database — it creates records):

```powershell
npm install playwright
npx playwright install chromium
$env:ADMIN_PASSWORD="<bootstrap password>"; node tests/e2e/ui_e2e.mjs screenshots
```
