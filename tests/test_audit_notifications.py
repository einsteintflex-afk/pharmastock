# Audit trail and notification centre.

from datetime import date, timedelta

import psycopg
import pytest

from tests.conftest import org_connection


def test_audit_records_old_and_new_values(api, db):
    api.put("/medicines/2", {"name": "Amoxicillin", "strength": "500 mg", "dosage_form": "Capsule",
                             "reorder_level": 35}, role="PHARMACIST")
    entry = db.execute("SELECT * FROM audit_log WHERE entity_type = 'medicine' AND entity_id = '2' "
                       "ORDER BY id DESC LIMIT 1").fetchone()
    assert entry["action"] == "UPDATE" and entry["username"] == "pharmacist"
    assert entry["old_value"] == {"reorder_level": 20} and entry["new_value"] == {"reorder_level": 35}
    assert entry["occurred_at"] is not None


def test_audit_api_filters(api):
    rows = api.get("/audit-log", params={"entity_type": "medicine", "entity_id": "2"}, role="MANAGER").json()
    assert rows and all(r["entity_type"] == "medicine" and r["entity_id"] == "2" for r in rows)
    logins = api.get("/audit-log", params={"action": "LOGIN"}).json()
    assert {r["username"] for r in logins} >= {"administrator", "pharmacist", "manager"}


def test_audit_log_is_append_only():
    with org_connection(1) as conn:
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("UPDATE audit_log SET action = 'X'")
        conn.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            conn.execute("DELETE FROM audit_log")


def test_no_passwords_in_audit(db):
    rows = db.execute("SELECT old_value::text || new_value::text AS t FROM audit_log "
                      "WHERE old_value IS NOT NULL OR new_value IS NOT NULL").fetchall()
    assert not any("password" in (r["t"] or "").lower() for r in rows)


def test_refresh_is_idempotent(api, db):
    api.post("/notifications/refresh")
    first = db.execute("SELECT COUNT(*) AS n FROM notifications").fetchone()["n"]
    api.post("/notifications/refresh")
    assert db.execute("SELECT COUNT(*) AS n FROM notifications").fetchone()["n"] == first


def test_expiry_and_stock_notifications_generated(api):
    notes = api.get("/notifications").json()
    keys = {(n["category"], n["entity_type"], n["entity_id"]) for n in notes}
    assert ("EXPIRY", "batch", "4") in keys          # PARA004 expired on 2026-09-05
    assert ("LOW_STOCK", "medicine", "4") in keys    # Ibuprofen out of stock


def test_notification_resolves_when_condition_clears(api, db):
    # Writing off the expired batch clears its expiry notification.
    batch = db.execute("SELECT id, quantity FROM batches WHERE batch_number = 'PARA004'").fetchone()
    assert batch["quantity"] > 0
    written_off = api.post("/stock-movements", {"batch_id": batch["id"], "movement_type": "EXPIRED",
                                                "quantity": batch["quantity"], "reason": "Disposed"})
    assert written_off.status_code == 200
    api.post("/notifications/refresh")
    row = db.execute("SELECT resolved_at FROM notifications WHERE dedupe_key = %s",
                     (f"expiry:EXPIRED:batch:{batch['id']}",)).fetchone()
    assert row["resolved_at"] is not None
    open_expiry = {n["entity_id"] for n in api.get("/notifications").json() if n["category"] == "EXPIRY"}
    assert str(batch["id"]) not in open_expiry


def test_read_state_is_per_user(api):
    notes = api.get("/notifications", role="PHARMACIST").json()
    target = notes[0]["id"]
    before_other = api.get("/notifications/unread-count", role="VIEWER").json()["unread"]
    assert api.post(f"/notifications/{target}/read", role="PHARMACIST").status_code == 200
    mine = next(n for n in api.get("/notifications", role="PHARMACIST").json() if n["id"] == target)
    assert mine["is_read"] is True
    assert api.get("/notifications/unread-count", role="VIEWER").json()["unread"] == before_other
    api.post("/notifications/read-all", role="PHARMACIST")
    assert api.get("/notifications/unread-count", role="PHARMACIST").json()["unread"] == 0
    assert api.post("/notifications/999999/read").status_code == 404


def test_locations(api, db):
    created = api.post("/locations", {"name": "Cold Room", "location_type": "COLD_CHAIN"}, role="MANAGER")
    assert created.status_code == 201
    loc = created.json()
    batch = api.post("/batches", {"medicine_id": 2, "batch_number": "COLD-1", "quantity": 10,
                                  "expiry_date": (date.today() + timedelta(days=300)).isoformat(),
                                  "unit_cost": 1.0, "location_id": loc["id"]})
    assert batch.status_code == 200
    value = api.get("/analytics/valuation").json()
    assert "Cold Room" in {v["location"] for v in value["by_location"]}
    assert api.put(f"/locations/{loc['id']}", {"name": "Cold Room", "location_type": "COLD_CHAIN",
                                               "is_active": False}, role="MANAGER").status_code == 400  # holds stock
    assert api.put(f"/locations/{loc['id']}", {"name": "Cold Room", "location_type": "COLD_CHAIN",
                                               "parent_id": loc["id"]}, role="MANAGER").status_code == 400
    assert api.post("/locations", {"name": "cold room", "location_type": "STORE"}, role="MANAGER").status_code == 409
    assert api.post("/locations", {"name": "Ward 3", "location_type": "WARD"}, role="PHARMACIST").status_code == 403
