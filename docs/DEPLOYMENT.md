# Deployment

Two supported ways to run PharmaStock. Both use PostgreSQL 16+ and HTTPS in production.

## A. Docker (recommended for new servers, staging and production)

Requirements: a Linux server with Docker Engine + Compose, ports 80/443 open, a DNS name.

```sh
git clone https://github.com/einsteintflex-afk/pharmastock.git && cd pharmastock
cp deploy/env.production.example .env.production      # fill in every change-me value
docker compose --env-file .env.production up -d --build
docker compose ps                                       # db, app (healthy), proxy, backup
```

What starts:

| Service | Image | Role |
|---|---|---|
| `db` | postgres:16 | Database. On first start `deploy/postgres-init.sh` creates three roles: `pharmastock_owner` (owns the schema, migrations only), `pharmastock_app` (the application: row privileges only, no superuser / BYPASSRLS) and `pharmastock_backup` (read-only, BYPASSRLS). Not exposed outside the Docker network. |
| `app` | built from `Dockerfile` | Applies migrations as the owner (`MIGRATION_DATABASE_URL`), grants the application role (`APP_DB_ROLE`), then serves the API and web app as `pharmastock_app` (2 workers, JSON logs, production mode). |
| `proxy` | caddy:2 | HTTPS with automatic Let's Encrypt certificates for `DOMAIN`, HTTP→HTTPS redirect, gzip. |
| `backup` | postgres:16 | Dump every `BACKUP_INTERVAL_HOURS` into `BACKUP_HOST_DIR`, keeps `BACKUP_RETENTION_DAYS`. |

First sign-in: `https://DOMAIN/app/` with `BOOTSTRAP_ADMIN_USERNAME` / `BOOTSTRAP_ADMIN_PASSWORD`,
then change the password and remove the bootstrap values from the env file.

**Staging** uses the same compose file on another host with `deploy/env.staging.example`
(`docker compose --env-file .env.staging up -d --build`). Test every release on staging
with a restored copy of a production backup before deploying it to production.

**Updating**: `git pull && docker compose --env-file .env.production up -d --build`.
Take a backup first (`docker compose exec backup sh /usr/local/bin/backup.sh`).

Alternative proxy: `deploy/nginx.conf` (nginx + certbot) adds per-IP rate limits in front
of the application's own limits.

## B. Existing Windows installation (upgrade in place)

```powershell
cd C:\Users\TOMMY\PHARMASTOCK
pg_dump -U postgres -d pharmastock -F c -f backups\before-upgrade.dump   # 1. backup
git fetch origin; git checkout claude/bold-goldberg-p2dvu3                  # 2. code
.venv\Scripts\activate; pip install -r requirements.txt
python -m backend.migrate status; python -m backend.migrate               # 3. schema
uvicorn backend.main:app --host 127.0.0.1 --port 8000                     # 4. run
```

Add to `.env` before starting the upgraded version:

```
SECRET_KEY=<48+ random characters: python -c "import secrets; print(secrets.token_urlsafe(48))">
```

Keep a copy of `SECRET_KEY` with the backups: without it, stored two-step verification
secrets and messaging credentials cannot be decrypted (users would need an MFA reset).

**Least-privilege roles (before adding a second organization or going to production).**
Row level security protects organizations from each other only when the application
connects as a restricted role:

```powershell
psql -U postgres -d pharmastock -v app_password='<strong password>' -f deploy\create_app_role.sql
# .env
MIGRATION_DATABASE_URL=postgresql://postgres:<pw>@localhost/pharmastock   # the current owner
APP_DB_ROLE=pharmastock_app
DATABASE_URL=postgresql://pharmastock_app:<strong password>@localhost/pharmastock
python -m backend.migrate            # grants pharmastock_app its privileges
```

Using a dedicated non-superuser owner role instead of `postgres` for
`MIGRATION_DATABASE_URL` is better still: `ALTER DATABASE pharmastock OWNER TO …` and
`ALTER TABLE … OWNER TO …` for every table (the loop in the old create_app_role.sql
shows how).

For access from other computers put IIS / nginx / Caddy with a certificate in front and
set `PHARMASTOCK_ENV=production` (secure cookies, HSTS, no `/docs`).

## Configuration (environment variables)

See `.env.example` for every variable with comments. Essentials:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection (restricted application role) |
| `MIGRATION_DATABASE_URL`, `APP_DB_ROLE` | Schema-owner connection for migrations; application role to grant after migrating |
| `SECRET_KEY` | Encrypts stored secrets (MFA, provider credentials); **required** in production |
| `PLATFORM_SESSION_HOURS`, `STEP_UP_MINUTES` | Platform administrator session length and step-up window |
| `WHATSAPP_*` | MedCart Tech's WhatsApp Business Platform number (Cloud API): phone number id, access token, app secret, verify token, receipt template |
| `PAYMENT_PROVIDER`, `PAYSTACK_SECRET_KEY` | `manual` (confirmed in the console) or `paystack` (verified webhooks) |
| `PHARMASTOCK_ENV` | `production` enables secure cookies, HSTS, disables `/docs`, refuses superuser DB roles |
| `APP_BASE_URL` | Public URL used in e-mailed links |
| `SMTP_*`, `SMS_*` | E-mail and SMS delivery (optional) |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | AI assistant via Claude (optional; built-in engine otherwise) |
| `LOG_FORMAT`, `LOG_LEVEL` | `json` for log shippers |
| `ERROR_WEBHOOK_URL` / `SENTRY_DSN` | Unhandled-error reporting |
| `METRICS_TOKEN` | Protects `/metrics` |
| `RATE_LIMIT_*` | Sign-in and API rate limits |

## External services (set up by MedCart Tech)

| Service | What to do | Webhook URL |
|---|---|---|
| WhatsApp Business Platform | Meta Business account → WhatsApp → phone number; create and get approval for the template `pharmastock_receipt` (body with 4 parameters: customer name, company name, total, receipt link) and optionally a thank-you template; permanent system-user access token | `https://DOMAIN/messaging/webhooks/whatsapp` (verify token = `WHATSAPP_VERIFY_TOKEN`; subscribe to `messages`) |
| Paystack | Secret key in `PAYSTACK_SECRET_KEY`, `PAYMENT_PROVIDER=paystack` | `https://DOMAIN/billing/webhooks/paystack` |
| SMS gateway | Any gateway accepting `{"to","message"}` JSON with a bearer token (`SMS_WEBHOOK_URL`) | — |
| SMTP | Any SMTP account (`SMTP_*`) | — |

Prices of plans and credit packages are set in the MedCart console (Plans / Payments & credits); nothing is sold until a price is set.

## Monitoring

- **Liveness**: `GET /health` (Docker health check uses it).
- **Readiness**: `GET /ready` → 503 while the database is down or migrations are pending.
- **Metrics**: `GET /metrics` (Prometheus text; `Authorization: Bearer METRICS_TOKEN`):
  request counts by route and status, latency histograms, uptime, connection pool.
- **Logs**: one JSON object per line with `request_id`; every response carries
  `X-Request-ID`, and 500 responses include it so a user report maps to one log line.
- **Errors**: set `ERROR_WEBHOOK_URL` (Slack / Teams / incident tool) or install
  `sentry-sdk` and set `SENTRY_DSN`.
- **Backups**: alert when the newest file in the backup directory is older than
  `BACKUP_INTERVAL_HOURS` + 2 h; run `deploy/verify_backup.sh` weekly (see BACKUP.md).

## CI/CD

`.github/workflows/ci.yml` runs on every push and pull request: the pytest suite against
PostgreSQL as a non-superuser role, JavaScript syntax and duplicate-function checks, the
browser end-to-end test, a Docker image build and a compose validation. Deploy by
pulling the tested commit on staging, then production (step A "Updating").
