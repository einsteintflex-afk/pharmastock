#!/bin/sh
# Wait for PostgreSQL, apply migrations (unless RUN_MIGRATIONS=false), start.
set -e

if [ -z "$DATABASE_URL" ]; then
    echo "DATABASE_URL is not set" >&2
    exit 1
fi

python - <<'PY'
import os, sys, time
import psycopg
for attempt in range(60):
    try:
        psycopg.connect(os.environ["DATABASE_URL"], connect_timeout=3).close()
        sys.exit(0)
    except psycopg.OperationalError:
        time.sleep(2)
sys.exit("Database not reachable after 60 attempts")
PY

if [ "${RUN_MIGRATIONS:-true}" = "true" ]; then
    python -m backend.migrate
fi

exec "$@"
