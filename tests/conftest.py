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

# The application reads configuration at import time.
os.environ["DATABASE_URL"] = TEST_URL
os.environ["BOOTSTRAP_ADMIN_USERNAME"] = ""
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = ""
os.environ["ANTHROPIC_API_KEY"] = ""
os.environ["NOTIFICATION_REFRESH_MINUTES"] = "600"
os.environ["LOG_LEVEL"] = "WARNING"

PASSWORD = "Test-pass-2026"
ROLES = ["ADMINISTRATOR", "MANAGER", "PHARMACIST", "PHARMACY_TECHNICIAN", "STOREKEEPER", "VIEWER"]


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


@pytest.fixture(scope="session", autouse=True)
def template_database():
    """Original schema + original test data + all migrations."""
    from backend import migrate

    _recreate(TEMPLATE_NAME)
    url = _url_for(TEMPLATE_NAME)
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(_dump_sql("db_schema.sql"))
        conn.execute(_dump_sql("db_testdata.sql"))
    migrate.migrate(url)

    from backend.security import hash_password

    password_hash = hash_password(PASSWORD)
    with psycopg.connect(url) as conn:
        for role in ROLES:
            conn.execute(
                "INSERT INTO users (username, full_name, role, password_hash) VALUES (%s, %s, %s, %s)",
                (role.lower(), f"Test {role.title().replace('_', ' ')}", role, password_hash),
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


@pytest.fixture
def db(api):
    """Direct database connection (for setting up history and verifying persistence)."""
    from psycopg.rows import dict_row

    with psycopg.connect(TEST_URL, row_factory=dict_row) as conn:
        yield conn
