# ============================================================
# DATABASE CONNECTION POOL AND TENANT CONTEXT
# ============================================================
# One pool for the whole application, opened at startup. Each request
# borrows a connection through get_db(); write endpoints commit explicitly,
# and anything uncommitted is rolled back when the request ends.
#
# Tenant isolation: tenant tables are protected by PostgreSQL row level
# security keyed on the `app.organization_id` setting. set_organization()
# sets it for the connection after authentication; the pool RESETs every
# connection when it is returned, so an organization never leaks from one
# request to the next.

from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

pool: ConnectionPool | None = None


def _reset(connection: psycopg.Connection) -> None:
    with connection.transaction():
        connection.execute("RESET ALL")


def open_pool() -> None:
    global pool
    pool = ConnectionPool(
        settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        kwargs={"row_factory": dict_row},
        reset=_reset,
        open=False,
    )
    pool.open(wait=True, timeout=10)


def close_pool() -> None:
    global pool
    if pool is not None:
        pool.close()
        pool = None


def get_db() -> Iterator[psycopg.Connection]:
    """FastAPI dependency: one transaction per request."""
    with pool.connection() as connection:
        yield connection


def set_organization(conn: psycopg.Connection, organization_id: int | None) -> None:
    """Scope this connection to one organization (row level security)."""
    conn.execute(
        "SELECT set_config('app.organization_id', %s, false)",
        ("" if organization_id is None else str(int(organization_id)),),
    )


def connect(organization_id: int | None = None) -> psycopg.Connection:
    """Stand-alone connection for scripts and background jobs."""
    conn = psycopg.connect(settings.database_url, row_factory=dict_row)
    if organization_id is not None:
        set_organization(conn, organization_id)
    return conn


@contextmanager
def organization_connection(organization_id: int) -> Iterator[psycopg.Connection]:
    """Pooled connection scoped to one organization (background jobs)."""
    with pool.connection() as conn:
        set_organization(conn, organization_id)
        yield conn


def active_organization_ids(conn: psycopg.Connection) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM organizations WHERE status IN ('TRIAL', 'ACTIVE') ORDER BY id"
    ).fetchall()
    return [row["id"] for row in rows]


def role_bypasses_rls(conn: psycopg.Connection) -> bool:
    row = conn.execute(
        "SELECT rolsuper OR rolbypassrls AS bypass FROM pg_roles WHERE rolname = current_user"
    ).fetchone()
    return bool(row and row["bypass"])
