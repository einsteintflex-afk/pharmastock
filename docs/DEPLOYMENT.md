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
| `db` | postgres:16 | Database. On first start `deploy/postgres-init.sh` creates `pharmastock_app` (owner, no superuser/BYPASSRLS) and `pharmastock_backup` (read-only, BYPASSRLS). Not exposed outside the Docker network. |
| `app` | built from `Dockerfile` | Applies migrations, then serves the API and web app (2 workers, JSON logs, production mode). |
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

Row level security only protects organizations from each other when the application
connects as a non-superuser role. For a single-pharmacy installation the development
setup works (a warning is logged); before adding a second organization or setting
`PHARMASTOCK_ENV=production`, run `deploy/create_app_role.sql` as `postgres` and point
`DATABASE_URL` at `pharmastock_app`.

For access from other computers put IIS / nginx / Caddy with a certificate in front and
set `PHARMASTOCK_ENV=production` (secure cookies, HSTS, no `/docs`).

## Configuration (environment variables)

See `.env.example` for every variable with comments. Essentials:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection (application role) |
| `PHARMASTOCK_ENV` | `production` enables secure cookies, HSTS, disables `/docs`, refuses superuser DB roles |
| `APP_BASE_URL` | Public URL used in e-mailed links |
| `SMTP_*`, `SMS_*` | E-mail and SMS delivery (optional) |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` | AI assistant via Claude (optional; built-in engine otherwise) |
| `LOG_FORMAT`, `LOG_LEVEL` | `json` for log shippers |
| `ERROR_WEBHOOK_URL` / `SENTRY_DSN` | Unhandled-error reporting |
| `METRICS_TOKEN` | Protects `/metrics` |
| `RATE_LIMIT_*` | Sign-in and API rate limits |

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
