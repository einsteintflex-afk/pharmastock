"""PharmaStock multi-tenant benchmark.

Builds a throw-away database with N organizations, each holding realistic
volumes of data, then measures the API with the restricted application role
(row level security active), in-process (FastAPI TestClient, one worker).

    python scripts/benchmark.py --orgs 10 100 1000 [--medicines 100] [--batches 2] [--sales 500]

Needs the same PostgreSQL access as the tests (TEST_DATABASE_URL owner role with
CREATEDB / CREATEROLE). Never touches the application database.

The numbers describe THIS machine and THIS data shape; they are not a
capacity guarantee. Results are printed as a Markdown table.
"""

import argparse
import os
import random
import statistics
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, urlunparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OWNER_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://pharma:pharma_dev@localhost:5432/pharmastock_bench")
DB_NAME = "pharmastock_bench"
APP_ROLE, APP_PASSWORD = "pharmastock_bench_app", "bench_app_pw"
PASSWORD = "Bench-pass-2026"


def url_for(name: str, user: str | None = None, password: str | None = None) -> str:
    parts = urlparse(OWNER_URL)
    netloc = parts.netloc
    if user:
        netloc = f"{user}:{password}@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    return urlunparse(parts._replace(path="/" + name, netloc=netloc))


# The application reads its configuration at import time.
os.environ.update({
    "DATABASE_URL": url_for(DB_NAME, APP_ROLE, APP_PASSWORD), "MIGRATION_DATABASE_URL": url_for(DB_NAME),
    "BOOTSTRAP_ADMIN_USERNAME": "", "BOOTSTRAP_ADMIN_PASSWORD": "", "ANTHROPIC_API_KEY": "",
    "RATE_LIMIT_API_PER_MINUTE": "0", "RATE_LIMIT_AUTH_PER_MINUTE": "0", "LOG_LEVEL": "WARNING",
    "NOTIFICATION_REFRESH_MINUTES": "100000", "DELIVERY_INTERVAL_SECONDS": "100000",
    "SECRET_KEY": "benchmark-secret-key-not-for-production-0123456789",
    "DB_POOL_MAX": "10",
})

import psycopg  # noqa: E402


def prepare_database() -> None:
    import re

    from backend import migrate

    def _dump_sql(filename: str) -> str:
        lines = [line for line in (ROOT / filename).read_text(encoding="utf-8").splitlines()
                 if not re.match(r"^\\(un)?restrict", line) and "transaction_timeout" not in line]
        return "\n".join(lines)

    with psycopg.connect(url_for("postgres"), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')
        conn.execute(f'CREATE DATABASE "{DB_NAME}"')
        if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone():
            conn.execute(f"CREATE ROLE {APP_ROLE} LOGIN PASSWORD '{APP_PASSWORD}' NOSUPERUSER NOBYPASSRLS")
    with psycopg.connect(url_for(DB_NAME), autocommit=True) as conn:
        conn.execute(_dump_sql("db_schema.sql"))
    migrate.migrate(url_for(DB_NAME), app_role=APP_ROLE)


def seed(first: int, last: int, medicines: int, batches_per_medicine: int, sales: int) -> None:
    """Organizations first..last (1-based numbering of benchmark orgs)."""
    from psycopg.rows import dict_row

    from backend.database import set_organization
    from backend.security import hash_password
    from backend.services import organizations

    password_hash = hash_password(PASSWORD)
    with psycopg.connect(url_for(DB_NAME), row_factory=dict_row) as conn:
        for n in range(first, last + 1):
            result = organizations.create(
                conn, name=f"Bench Pharmacy {n}", org_type="COMMUNITY_PHARMACY", plan="ENTERPRISE", status="ACTIVE",
                admin_username=f"bench{n}", admin_full_name=f"Bench Owner {n}", admin_password_hash=password_hash,
                return_to_organization=None)
            org_id = result["organization"]["id"]
            set_organization(conn, org_id)
            conn.execute("UPDATE users SET must_change_password = false WHERE id = %s", (result["administrator"]["id"],))
            user_id = result["administrator"]["id"]
            location = conn.execute("SELECT id FROM locations LIMIT 1").fetchone()["id"]
            conn.execute(
                """
                INSERT INTO medicines (name, strength, dosage_form, reorder_level, selling_price, gtin)
                SELECT 'Medicine ' || g, (g %% 20 * 50 + 50) || ' mg', 'Tablet', 20, 1 + g %% 30,
                       lpad((%s::bigint * 100000 + g)::text, 14, '0')
                FROM generate_series(1, %s) g
                """, (org_id, medicines))
            conn.execute(
                """
                INSERT INTO batches (medicine_id, batch_number, quantity, expiry_date, location_id, unit_cost, received_date)
                SELECT m.id, 'B' || m.id || '-' || b, 200 + (m.id * b) %% 300,
                       CURRENT_DATE + ((m.id * 37 + b * 101) %% 700 - 60), %s, 0.5 + (m.id %% 10), CURRENT_DATE - 120
                FROM medicines m CROSS JOIN generate_series(1, %s) b
                """, (location, batches_per_medicine))
            conn.execute(
                """
                INSERT INTO stock_movements (batch_id, movement_type, quantity, reason, user_id, movement_date)
                SELECT id, 'RECEIVED', quantity, 'Opening stock', %s, CURRENT_TIMESTAMP - interval '120 days' FROM batches
                """, (user_id,))
            # Sales: DISPENSED movements spread over 90 days, then batch quantities follow the ledger.
            conn.execute(
                """
                INSERT INTO stock_movements (batch_id, movement_type, quantity, reason, user_id, movement_date)
                SELECT (SELECT id FROM batches ORDER BY id OFFSET (g * 7919) %% (SELECT COUNT(*) FROM batches) LIMIT 1),
                       'DISPENSED', 1 + g %% 3, 'Sale', %s, CURRENT_TIMESTAMP - make_interval(hours => g %% 2160)
                FROM generate_series(1, %s) g
                """, (user_id, sales))
            conn.execute(
                """
                UPDATE batches b SET quantity = GREATEST(0, s.total) FROM (
                    SELECT batch_id, SUM(CASE WHEN movement_type = 'RECEIVED' THEN quantity ELSE -quantity END) AS total
                    FROM stock_movements GROUP BY batch_id) s WHERE s.batch_id = b.id
                """)
            conn.commit()
            set_organization(conn, None)
        conn.execute("ANALYZE")
        conn.commit()


def measure(client, orgs: int, samples: int = 25) -> dict:
    from fastapi.testclient import TestClient  # noqa: F401

    rng = random.Random(42)
    chosen = rng.sample(range(1, orgs + 1), min(5, orgs))
    tokens = []
    login_times = []
    for n in chosen:
        started = time.perf_counter()
        response = client.post("/auth/login", json={"username": f"bench{n}", "password": PASSWORD})
        login_times.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 200, response.text
        tokens.append(response.json()["token"])
        client.cookies.clear()
    endpoints = [
        ("GET /medicines", "get", "/medicines", None),
        ("GET /inventory (page)", "get", "/inventory?limit=50", None),
        ("GET /dashboard", "get", "/dashboard", None),
        ("GET /attention", "get", "/attention", None),
        ("GET /search", "get", "/search?q=Medicine 1", None),
        ("GET /analytics/reorder", "get", "/analytics/reorder", None),
        ("GET /reports/valuation", "get", "/reports/valuation", None),
        ("POST /dispensations", "post", "/dispensations", "sale"),
    ]
    results = {"POST /auth/login (scrypt)": login_times}
    for label, method, path, body in endpoints:
        times = []
        for i in range(samples):
            headers = {"Authorization": f"Bearer {tokens[i % len(tokens)]}"}
            payload = None
            if body == "sale":
                medicine = client.get("/medicines?limit=1", headers=headers).json()
                payload = {"dispense_type": "OTC", "payment_method": "CASH",
                           "items": [{"medicine_id": rng.choice(medicine)["id"] if medicine else 1, "quantity": 1}]}
            started = time.perf_counter()
            response = getattr(client, method)(path, headers=headers, **({"json": payload} if payload else {}))
            times.append((time.perf_counter() - started) * 1000)
            assert response.status_code in (200, 201), (label, response.status_code, response.text[:200])
        results[label] = times
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--orgs", type=int, nargs="+", default=[10, 100, 1000])
    parser.add_argument("--medicines", type=int, default=100)
    parser.add_argument("--batches", type=int, default=2)
    parser.add_argument("--sales", type=int, default=500)
    args = parser.parse_args()

    prepare_database()
    from fastapi.testclient import TestClient

    from backend.main import app

    report = []
    seeded = 0
    for target in sorted(args.orgs):
        started = time.perf_counter()
        seed(seeded + 1, target, args.medicines, args.batches, args.sales)
        seed_seconds = time.perf_counter() - started
        seeded = target
        with psycopg.connect(url_for(DB_NAME)) as conn:
            size = conn.execute("SELECT pg_size_pretty(pg_database_size(current_database()))").fetchone()[0]
            rows = conn.execute("SELECT (SELECT COUNT(*) FROM stock_movements), (SELECT COUNT(*) FROM batches)").fetchone()
        with TestClient(app) as client:
            results = measure(client, target)
        report.append((target, size, rows, seed_seconds, results))
        print(f"{target} organizations seeded in {seed_seconds:.0f}s; database {size}", flush=True)

    print("\n| Organizations | Database | Movements | Batches | Endpoint | p50 ms | p95 ms | max ms |")
    print("|---:|---:|---:|---:|---|---:|---:|---:|")
    for target, size, rows, _, results in report:
        for label, times in results.items():
            ordered = sorted(times)
            p95 = ordered[max(0, int(len(ordered) * 0.95) - 1)]
            print(f"| {target} | {size} | {rows[0]:,} | {rows[1]:,} | {label} | {statistics.median(times):.0f} | "
                  f"{p95:.0f} | {max(times):.0f} |")


if __name__ == "__main__":
    main()
