# Migrations: existing data preserved, ledger reconciled, fresh install works,
# re-running is a no-op.

import psycopg
from psycopg.rows import dict_row

from backend import migrate
from tests.conftest import _recreate, _url_for


def test_existing_data_preserved(db):
    assert db.execute("SELECT count(*) AS n FROM medicines").fetchone()["n"] == 3
    assert db.execute("SELECT count(*) AS n FROM batches").fetchone()["n"] == 6
    assert db.execute("SELECT count(*) AS n FROM suppliers").fetchone()["n"] == 1
    assert db.execute("SELECT count(*) AS n FROM purchase_orders").fetchone()["n"] == 1
    assert db.execute("SELECT count(*) AS n FROM purchase_receipts").fetchone()["n"] == 1

    quantities = {r["batch_number"]: r["quantity"] for r in db.execute("SELECT batch_number, quantity FROM batches")}
    assert quantities == {"PARA001": 100, "PARA002": 50, "PARA003": 30, "PARA004": 10,
                          "AMOX001": 180, "AMOX002": 100}

    # The two original movements are untouched.
    original = db.execute(
        "SELECT id, batch_id, movement_type, quantity FROM stock_movements WHERE id IN (1, 2) ORDER BY id"
    ).fetchall()
    assert [(r["batch_id"], r["movement_type"], r["quantity"]) for r in original] == [
        (5, "DISPENSED", 20), (6, "RECEIVED", 100)]


def test_cost_backfilled_only_where_traceable(db):
    rows = {r["batch_number"]: r for r in db.execute(
        "SELECT batch_number, unit_cost, supplier_id, received_date, location_id FROM batches")}
    assert float(rows["AMOX002"]["unit_cost"]) == 5.50
    assert rows["AMOX002"]["supplier_id"] == 1
    assert str(rows["AMOX002"]["received_date"]) == "2026-09-16"
    for name in ("PARA001", "PARA002", "PARA003", "PARA004", "AMOX001"):
        assert rows[name]["unit_cost"] is None  # never guessed
    assert {r["location_id"] for r in rows.values()} == {1}


def test_ledger_reconciled_after_migration(api):
    body = api.get("/stock-reconciliation").json()
    assert body == {"reconciled": True, "mismatches": []}


def test_rerun_is_noop():
    assert migrate.migrate(_url_for("pharmastock_test")) == []


def test_fresh_install_creates_full_schema():
    name = "pharmastock_test_fresh"
    _recreate(name)
    url = _url_for(name)
    applied = migrate.migrate(url)
    assert applied[0] == "0001_baseline"  # run, not stamped, on an empty database
    with psycopg.connect(url, row_factory=dict_row) as conn:
        tables = {r["table_name"] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'")}
    for table in ("medicines", "batches", "suppliers", "purchase_orders", "purchase_order_items",
                  "purchase_receipts", "stock_movements", "locations", "users", "sessions", "audit_log",
                  "notifications", "notification_reads", "app_settings", "schema_migrations"):
        assert table in tables
    with psycopg.connect(_url_for("postgres"), autocommit=True) as conn:
        conn.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


def test_modified_migration_detected(tmp_path, monkeypatch):
    for path in migrate.MIGRATIONS_DIR.glob("*.sql"):
        (tmp_path / path.name).write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "0002_integrity_constraints.sql").write_text("-- edited\n", encoding="utf-8")
    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path)
    try:
        migrate.migrate(_url_for("pharmastock_test"))
        raise AssertionError("expected RuntimeError")
    except RuntimeError as error:
        assert "modified" in str(error)
