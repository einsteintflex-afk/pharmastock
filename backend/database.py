# ============================================================
# DATABASE CONNECTION POOL
# ============================================================
# One pool for the whole application, opened at startup. Each request
# borrows a connection through get_db(); write endpoints commit explicitly,
# and anything uncommitted is rolled back when the request ends.

from collections.abc import Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import settings

pool: ConnectionPool | None = None


def open_pool() -> None:
    global pool
    pool = ConnectionPool(
        settings.database_url,
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        kwargs={"row_factory": dict_row},
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


def connect() -> psycopg.Connection:
    """Stand-alone connection for scripts and migrations."""
    return psycopg.connect(settings.database_url, row_factory=dict_row)
