# ============================================================
# TEST FIXTURES
# ============================================================
# Tests run against a throw-away PostgreSQL database built from the real
# project dumps (db_schema.sql + db_testdata.sql) plus all migrations, so
# every test exercises the actual upgrade path of the existing data.
#
# Requirements: a PostgreSQL server and a role allowed to CREATE DATABASE.
#   TEST_DATABASE_URL   default postgresql://pharma:pharma_dev@localhost:5432/pharmastock_test
# The production database is never touched.

import os
import re
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
TEST_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql://pharma:pharma_dev@localhost:5432/pharmastock_test"
)
TEMPLATE_NAME = urlparse(TEST_URL).path.lstrip("/") + "_template"
TEST_NAME = urlparse(TEST_URL).path.lstrip("/")

# The API runs as a restricted, non-owner application role (no superuser, no
# BYPASSRLS, row privileges only), exactly as in production; fixtures and
# migrations use the owner connection TEST_URL. Creating the role needs
# CREATEROLE (or set TEST_APP_ROLE=owner to run the API as the owner).
APP_ROLE = os.environ.get("TEST_APP_ROLE", "pharmastock_test_app")
APP_PASSWORD = "test_app_role_pw"


def _app_url() -> str:
    if APP_ROLE == "owner":
        return TEST_URL
    parts = urlparse(TEST_URL)
    netloc = f"{APP_ROLE}:{APP_PASSWORD}@{parts.hostname}" + (f":{parts.port}" if parts.port else "")
    return urlunparse(parts._replace(netloc=netloc))


# The application reads configuration at import time.
os.environ["DATABASE_URL"] = _app_url()
os.environ["MIGRATION_DATABASE_URL"] = TEST_URL
os.environ["BOOTSTRAP_ADMIN_USERNAME"] = ""
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["NOTIFICATION_REFRESH_MINUTES"] = "600"
os.environ["DELIVERY_INTERVAL_SECONDS"] = "36000"
os.environ["SMTP_HOST"] = ""
os.environ["SMS_PROVIDER"] = "none"
# Limits are exercised by dedicated tests (which lower them); the suite itself is fast.
os.environ["RATE_LIMIT_API_PER_MINUTE"] = "0"
os.environ["RATE_LIMIT_AUTH_PER_MINUTE"] = "0"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["SECRET_KEY"] = "test-secret-key-for-encryption-only-not-production-0123456789"

PASSWORD = "Test-pass-2026"
ROLES = ["ADMINISTRATOR", "MANAGER", "PHARMACIST", "PHARMACY_TECHNICIAN", "STOREKEEPER", "VIEWER",
         "OWNER", "INVENTORY_OFFICER", "PURCHASING_OFFICER", "CASHIER", "AUDITOR"]
# The administrator is also the platform administrator and signs in with a
# second factor (TOTP) generated from this secret.
ADMIN_TOTP_SECRET = "JBSWY3DPEHPK3PXPJBSWY3DPEHPK3PXP"


def _url_for(name: str) -> str:
    parts = urlparse(TEST_URL)
    return urlunparse(parts._replace(path="/" + name))


def _maintenance_url() -> str:
    return _url_for("postgres")


def _dump_sql(filename: str) -> str:
    """Load a pg_dump file, removing lines newer PostgreSQL versions emit that
    older servers reject (\\restrict, transaction_timeout)."""
    text = (ROOT / filename).read_text(encoding="utf-8")
    lines = [
        line for line in text.splitlines()
        if not re.match(r"^\\(un)?restrict", line) and "transaction_timeout" not in line
    ]
    return "\n".join(lines)


def _recreate(name: str, template: str | None = None) -> None:
    with psycopg.connect(_maintenance_url(), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        if template:
            conn.execute(f'CREATE DATABASE "{name}" TEMPLATE "{template}"')
        else:
            conn.execute(f'CREATE DATABASE "{name}"')


def _ensure_app_role() -> None:
    if APP_ROLE == "owner":
        return
    with psycopg.connect(_maintenance_url(), autocommit=True) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (APP_ROLE,)).fetchone()
        if not exists:
            conn.execute(f'CREATE ROLE "{APP_ROLE}" LOGIN PASSWORD \'{APP_PASSWORD}\' '
                         "NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE")


@pytest.fixture(scope="session", autouse=True)
def template_database():
    """Original schema + original test data + all migrations."""
    from backend import migrate

    _recreate(TEMPLATE_NAME)
    _ensure_app_role()
    url = _url_for(TEMPLATE_NAME)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(_dump_sql("db_schema.sql"))
        conn.execute(_dump_sql("db_testdata.sql"))
    migrate.migrate(url, app_role=None if APP_ROLE == "owner" else APP_ROLE)

    from backend import crypto
    from backend.security import hash_password

    password_hash = hash_password(PASSWORD)
    with psycopg.connect(url) as conn:
        for role in ROLES:
            admin = role == "ADMINISTRATOR"
            conn.execute(
                "INSERT INTO users (username, full_name, role, password_hash, organization_id, is_platform_admin, "
                "mfa_enabled, mfa_secret_encrypted, mfa_enabled_at) "
                "VALUES (%s, %s, %s, %s, 1, %s, %s, %s, CASE WHEN %s THEN CURRENT_TIMESTAMP END)",
                (role.lower(), f"Test {role.title().replace('_', ' ')}", role, password_hash,
                 admin, admin, crypto.encrypt(ADMIN_TOTP_SECRET) if admin else None, admin),
            )
        conn.commit()
    yield
    with psycopg.connect(_maintenance_url(), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{TEST_NAME}" WITH (FORCE)')
        conn.execute(f'DROP DATABASE IF EXISTS "{TEMPLATE_NAME}" WITH (FORCE)')


class Api:
    """Test client wrapper with per-role bearer tokens."""

    def __init__(self, client):
        self.client = client
        self.tokens: dict[str, str] = {}

    def token(self, role: str) -> str:
        if role not in self.tokens:
            response = self.client.post("/auth/login", json={"username": role.lower(), "password": PASSWORD})
            assert response.status_code == 200, response.text
            if response.json().get("mfa_required"):
                response = self.client.post("/auth/mfa/verify", json={
                    "challenge_token": response.json()["challenge_token"], "code": totp(role.lower())})
                assert response.status_code == 200, response.text
            self.tokens[role] = response.json()["token"]
            self.client.cookies.clear()
        return self.tokens[role]

    def headers(self, role: str) -> dict:
        return {"Authorization": f"Bearer {self.token(role)}"}

    def get(self, path, role="ADMINISTRATOR", **kwargs):
        return self.client.get(path, headers=self.headers(role), **kwargs)

    def post(self, path, json=None, role="ADMINISTRATOR", **kwargs):
        return self.client.post(path, json=json, headers=self.headers(role), **kwargs)

    def put(self, path, json=None, role="ADMINISTRATOR", **kwargs):
        return self.client.put(path, json=json, headers=self.headers(role), **kwargs)

    def delete(self, path, role="ADMINISTRATOR", **kwargs):
        return self.client.delete(path, headers=self.headers(role), **kwargs)


@pytest.fixture(scope="module")
def api(template_database):
    """A fresh copy of the migrated database and a running app per test module."""
    from fastapi.testclient import TestClient

    from backend.main import app

    _recreate(TEST_NAME, TEMPLATE_NAME)
    with TestClient(app) as client:
        yield Api(client)


def totp(username: str = "administrator", secret: str = ADMIN_TOTP_SECRET) -> str:
    """Current TOTP code for a test user. The last used time step is cleared
    first, so a test may sign in or step up several times within 30 seconds
    (replay protection is exercised by its own test)."""
    from backend import crypto

    with psycopg.connect(TEST_URL) as conn:
        conn.execute("UPDATE users SET mfa_last_counter = NULL WHERE username = %s", (username,))
        conn.commit()
    return crypto.totp_now(secret)


def org_connection(organization_id: int = 1, **kwargs):
    """Direct connection scoped to one organization (row level security)."""
    conn = psycopg.connect(TEST_URL, **kwargs)
    conn.execute("SELECT set_config('app.organization_id', %s, false)", (str(organization_id),))
    conn.commit()
    return conn


@pytest.fixture
def db(api):
    """Direct database connection for organization 1 (setup and persistence checks)."""
    from psycopg.rows import dict_row

    with org_connection(1, row_factory=dict_row) as conn:
        yield conn
