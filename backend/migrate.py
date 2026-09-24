# ============================================================
# DATABASE MIGRATIONS
# ============================================================
# Versioned SQL migrations in backend/migrations, applied in order, each in
# its own transaction, and recorded in schema_migrations with a checksum.
#
#   python -m backend.migrate            apply pending migrations
#   python -m backend.migrate status     list applied / pending migrations
#
# An existing PharmaStock database (created before migrations existed) is
# detected and 0001_baseline is STAMPED as applied without running it, so the
# original tables and data are never touched by the baseline.

import hashlib
import logging
import sys
from pathlib import Path

import psycopg
import psycopg.sql
from psycopg.rows import tuple_row

from .config import settings

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
BASELINE_VERSION = "0001"

logger = logging.getLogger("pharmastock.migrate")


def _migration_files() -> list[tuple[str, str, Path]]:
    files = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        version, _, name = path.stem.partition("_")
        files.append((version, name, path))
    return files


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ensure_table(connection: psycopg.Connection) -> None:
    # The application role may not create tables: only create when missing.
    exists = connection.cursor(row_factory=tuple_row).execute(
        "SELECT to_regclass('public.schema_migrations') IS NOT NULL").fetchone()[0]
    if exists:
        return
    connection.execute("""
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version varchar(20) PRIMARY KEY,
            name varchar(200) NOT NULL,
            checksum char(64) NOT NULL,
            applied_at timestamp without time zone NOT NULL DEFAULT CURRENT_TIMESTAMP,
            stamped boolean NOT NULL DEFAULT false
        );
    """)
    connection.commit()


def _applied(connection: psycopg.Connection) -> dict[str, str]:
    rows = connection.cursor(row_factory=tuple_row).execute(
        "SELECT version, checksum FROM schema_migrations"
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _legacy_schema_present(connection: psycopg.Connection) -> bool:
    row = connection.cursor(row_factory=tuple_row).execute(
        "SELECT to_regclass('public.medicines')"
    ).fetchone()
    return row[0] is not None


def pending_migrations(connection: psycopg.Connection) -> list[str]:
    _ensure_table(connection)
    applied = _applied(connection)
    pending = []
    for version, name, path in _migration_files():
        if version not in applied:
            if version == BASELINE_VERSION and _legacy_schema_present(connection):
                continue  # would be stamped, not run
            pending.append(f"{version}_{name}")
        elif applied[version] != _checksum(path):
            raise RuntimeError(
                f"Migration {version}_{name} was modified after it was applied. "
                "Applied migrations must never be edited; add a new migration instead."
            )
    return pending


# Least privilege: migrations run as the schema OWNER (MIGRATION_DATABASE_URL);
# the application connects as a separate role that can only read and write
# rows. The audit trails are append-only for it, and it can never change the
# schema or the migration history.
_APPEND_ONLY = ("audit_log", "platform_audit", "security_events")


def grant_app_role(connection: psycopg.Connection, role: str) -> None:
    """(Re)apply the application role's privileges after migrations."""
    ident = psycopg.sql.Identifier(role)
    statements = [
        "GRANT USAGE ON SCHEMA public TO {r}",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {r}",
        "GRANT USAGE, SELECT, UPDATE ON ALL SEQUENCES IN SCHEMA public TO {r}",
        "REVOKE INSERT, UPDATE, DELETE ON schema_migrations FROM {r}",
        "REVOKE UPDATE, DELETE ON " + ", ".join(_APPEND_ONLY) + " FROM {r}",
        "REVOKE TRUNCATE, REFERENCES, TRIGGER ON ALL TABLES IN SCHEMA public FROM {r}",
    ]
    with connection.transaction():
        for statement in statements:
            connection.execute(psycopg.sql.SQL(statement).format(r=ident))


def migrate(database_url: str | None = None, app_role: str | None = None) -> list[str]:
    """Apply all pending migrations (as the schema owner) and grant the
    application role its row-level privileges. Returns the versions applied."""
    applied_now = []
    app_role = app_role if app_role is not None else settings.app_db_role

    with psycopg.connect(database_url or settings.migration_database_url or settings.database_url) as connection:
        _ensure_table(connection)
        applied = _applied(connection)

        for version, name, path in _migration_files():
            checksum = _checksum(path)

            if version in applied:
                if applied[version] != checksum:
                    raise RuntimeError(
                        f"Migration {version}_{name} was modified after it was applied."
                    )
                continue

            if version == BASELINE_VERSION and _legacy_schema_present(connection):
                connection.execute(
                    "INSERT INTO schema_migrations (version, name, checksum, stamped) VALUES (%s, %s, %s, true)",
                    (version, name, checksum),
                )
                connection.commit()
                logger.info("Stamped %s_%s (existing schema detected)", version, name)
                applied_now.append(f"{version}_{name} (stamped)")
                continue

            logger.info("Applying %s_%s", version, name)
            try:
                with connection.transaction():
                    connection.execute(path.read_text(encoding="utf-8"))
                    connection.execute(
                        "INSERT INTO schema_migrations (version, name, checksum) VALUES (%s, %s, %s)",
                        (version, name, checksum),
                    )
            except psycopg.Error as error:
                raise RuntimeError(f"Migration {version}_{name} failed and was rolled back: {error}") from error

            applied_now.append(f"{version}_{name}")

        if app_role:
            grant_app_role(connection, app_role)
            logger.info("Privileges granted to application role %s", app_role)

    return applied_now


def status(database_url: str | None = None) -> list[dict]:
    with psycopg.connect(database_url or settings.migration_database_url or settings.database_url) as connection:
        _ensure_table(connection)
        rows = connection.execute(
            "SELECT version, name, applied_at, stamped FROM schema_migrations"
        ).fetchall()
        applied = {row[0]: row for row in rows}
        result = []
        for version, name, _ in _migration_files():
            row = applied.get(version)
            result.append({
                "migration": f"{version}_{name}",
                "state": ("stamped" if row[3] else "applied") if row else "pending",
                "applied_at": str(row[2]) if row else None,
            })
        return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if len(sys.argv) > 1 and sys.argv[1] == "status":
        for item in status():
            print(f"{item['migration']:<55} {item['state']:<8} {item['applied_at'] or ''}")
    else:
        done = migrate()
        print("Applied:" if done else "Database is up to date.")
        for item in done:
            print("  ", item)
