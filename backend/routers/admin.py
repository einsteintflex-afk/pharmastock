# ============================================================
# NOTIFICATIONS, AUDIT TRAIL, SETTINGS AND LOCATIONS
# ============================================================

import json
from datetime import date, timedelta
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from .. import audit
from ..database import get_db
from ..migrate import status as migration_status
from ..pagination import set_total
from ..schemas import Name150
from ..security import CurrentUser, require
from ..services import app_settings, notifications, plans

router = APIRouter()

LocationType = Literal["PHARMACY", "STORE", "CENTRAL_STORE", "COLD_CHAIN", "WARD", "BRANCH", "DEPARTMENT"]


# ------------------------------------------------------------
# Notifications
# ------------------------------------------------------------

@router.get("/notifications", tags=["Notifications"])
def list_notifications(include_resolved: bool = False, unread_only: bool = False,
                       limit: int = Query(default=100, gt=0, le=500),
                       user: CurrentUser = Depends(require("notifications.read")),
                       conn: psycopg.Connection = Depends(get_db)):
    return notifications.list_for_user(conn, user.id, include_resolved=include_resolved,
                                       unread_only=unread_only, limit=limit)


@router.get("/notifications/unread-count", tags=["Notifications"])
def unread_count(user: CurrentUser = Depends(require("notifications.read")),
                 conn: psycopg.Connection = Depends(get_db)):
    return {"unread": notifications.unread_count(conn, user.id)}


@router.post("/notifications/{notification_id}/read", tags=["Notifications"])
def mark_read(notification_id: int, user: CurrentUser = Depends(require("notifications.read")),
              conn: psycopg.Connection = Depends(get_db)):
    if conn.execute("SELECT 1 FROM notifications WHERE id = %s", (notification_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    conn.execute(
        "INSERT INTO notification_reads (notification_id, user_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
        (notification_id, user.id),
    )
    conn.commit()
    return {"id": notification_id, "is_read": True}


@router.post("/notifications/read-all", tags=["Notifications"])
def mark_all_read(user: CurrentUser = Depends(require("notifications.read")),
                  conn: psycopg.Connection = Depends(get_db)):
    count = conn.execute(
        """
        INSERT INTO notification_reads (notification_id, user_id)
        SELECT id, %s FROM notifications
        WHERE resolved_at IS NULL OR category IN ('PURCHASING', 'RECEIVING', 'TRANSFERS')
        ON CONFLICT DO NOTHING
        """,
        (user.id,),
    ).rowcount
    conn.commit()
    return {"marked_read": count}


@router.post("/notifications/refresh", tags=["Notifications"])
def refresh_notifications(user: CurrentUser = Depends(require("notifications.read")),
                          conn: psycopg.Connection = Depends(get_db)):
    """Re-check expiry and stock conditions now (also runs automatically)."""
    result = notifications.refresh(conn)
    conn.commit()
    return result


# ------------------------------------------------------------
# Audit trail
# ------------------------------------------------------------

@router.get("/audit-log", tags=["Audit"])
def audit_log(response: Response,
              entity_type: str | None = Query(default=None, max_length=50),
              entity_id: str | None = Query(default=None, max_length=64),
              user_id: int | None = None,
              action: str | None = Query(default=None, max_length=50),
              date_from: date | None = None, date_to: date | None = None,
              limit: int = Query(default=200, gt=0, le=5000),
              offset: int = Query(default=0, ge=0),
              user: CurrentUser = Depends(require("audit.read")),
              conn: psycopg.Connection = Depends(get_db)):
    rows = conn.execute(
        """
        SELECT id, occurred_at, user_id, username, action, entity_type, entity_id,
               old_value, new_value, ip_address, COUNT(*) OVER () AS total_count
        FROM audit_log
        WHERE (%(entity_type)s::text IS NULL OR entity_type = %(entity_type)s::text)
          AND (%(entity_id)s::text IS NULL OR entity_id = %(entity_id)s::text)
          AND (%(user_id)s::int IS NULL OR user_id = %(user_id)s::int)
          AND (%(action)s::text IS NULL OR action = %(action)s::text)
          AND (%(date_from)s::date IS NULL OR occurred_at >= %(date_from)s::date)
          AND (%(date_to)s::date IS NULL OR occurred_at < %(date_to_next)s::date)
        ORDER BY occurred_at DESC, id DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {"entity_type": entity_type, "entity_id": entity_id, "user_id": user_id, "action": action,
         "date_from": date_from, "date_to": date_to,
         "date_to_next": (date_to + timedelta(days=1)) if date_to else None, "limit": limit,
         "offset": offset},
    ).fetchall()
    return set_total(response, rows)


# ------------------------------------------------------------
# Settings
# ------------------------------------------------------------

@router.get("/settings", tags=["Settings"])
def get_settings(user: CurrentUser = Depends(require("inventory.read")),
                 conn: psycopg.Connection = Depends(get_db)):
    rows = conn.execute(
        """
        SELECT app_settings.key, app_settings.value, app_settings.description, app_settings.updated_at,
               users.full_name AS updated_by
        FROM app_settings LEFT JOIN users ON users.id = app_settings.updated_by
        ORDER BY app_settings.key
        """
    ).fetchall()
    return rows


@router.put("/settings", tags=["Settings"])
def update_settings(changes: dict, user: CurrentUser = Depends(require("settings.manage")),
                    conn: psycopg.Connection = Depends(get_db)):
    current = app_settings.get_all(conn)
    cleaned = app_settings.validate(changes, current)
    for key, value in cleaned.items():
        if current.get(key) == value:
            continue
        conn.execute(
            "UPDATE app_settings SET value = %s, updated_at = CURRENT_TIMESTAMP, updated_by = %s WHERE key = %s",
            (json.dumps(value), user.id, key),
        )
        audit.record(conn, user, "UPDATE", "setting", key, {"value": current.get(key)}, {"value": value})
    # Thresholds affect notifications immediately.
    notifications.refresh(conn)
    conn.commit()
    return app_settings.get_all(conn)


@router.get("/system/migrations", tags=["Settings"])
def migrations(user: CurrentUser = Depends(require("settings.manage"))):
    return migration_status()


# ------------------------------------------------------------
# Locations
# ------------------------------------------------------------

class LocationBody(BaseModel):
    name: Name150
    location_type: LocationType
    parent_id: int | None = None
    is_active: bool = True


@router.get("/locations", tags=["Locations"])
def list_locations(user: CurrentUser = Depends(require("inventory.read")),
                   conn: psycopg.Connection = Depends(get_db)):
    return conn.execute(
        """
        SELECT locations.*, parent.name AS parent_name,
               COUNT(batches.id) FILTER (WHERE batches.quantity > 0) AS batches_in_stock,
               COALESCE(SUM(batches.quantity), 0)::int AS units
        FROM locations
        LEFT JOIN locations parent ON parent.id = locations.parent_id
        LEFT JOIN batches ON batches.location_id = locations.id
        GROUP BY locations.id, parent.name
        ORDER BY locations.id
        """
    ).fetchall()


def _validate_parent(conn, parent_id: int | None, location_id: int | None = None) -> None:
    if parent_id is None:
        return
    if parent_id == location_id:
        raise HTTPException(status_code=400, detail="A location cannot be its own parent")
    # Walk up the tree to prevent cycles.
    seen, current = set(), parent_id
    while current is not None:
        if current in seen or current == location_id:
            raise HTTPException(status_code=400, detail="This parent would create a loop")
        seen.add(current)
        row = conn.execute("SELECT parent_id FROM locations WHERE id = %s", (current,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Parent location not found")
        current = row["parent_id"]


@router.post("/locations", tags=["Locations"], status_code=201)
def create_location(body: LocationBody, user: CurrentUser = Depends(require("locations.manage")),
                    conn: psycopg.Connection = Depends(get_db)):
    _validate_parent(conn, body.parent_id)
    active = conn.execute("SELECT COUNT(*) AS n FROM locations WHERE is_active").fetchone()["n"]
    plans.check_limit(conn, user.organization_id, "max_locations", active)
    if conn.execute("SELECT 1 FROM locations WHERE lower(btrim(name)) = lower(%s)", (body.name,)).fetchone():
        raise HTTPException(status_code=409, detail="A location with this name already exists")
    row = conn.execute(
        """
        INSERT INTO locations (name, location_type, parent_id, is_active) VALUES (%s, %s, %s, %s)
        RETURNING *
        """,
        (body.name, body.location_type, body.parent_id, body.is_active),
    ).fetchone()
    audit.record(conn, user, "CREATE", "location", row["id"], None, dict(row))
    conn.commit()
    return row


@router.put("/locations/{location_id}", tags=["Locations"])
def update_location(location_id: int, body: LocationBody,
                    user: CurrentUser = Depends(require("locations.manage")),
                    conn: psycopg.Connection = Depends(get_db)):
    old = conn.execute("SELECT * FROM locations WHERE id = %s FOR UPDATE", (location_id,)).fetchone()
    if old is None:
        raise HTTPException(status_code=404, detail="Location not found")
    _validate_parent(conn, body.parent_id, location_id)
    if conn.execute(
        "SELECT 1 FROM locations WHERE lower(btrim(name)) = lower(%s) AND id <> %s", (body.name, location_id)
    ).fetchone():
        raise HTTPException(status_code=409, detail="A location with this name already exists")
    if not body.is_active and old["is_active"]:
        in_stock = conn.execute(
            "SELECT COALESCE(SUM(quantity), 0) AS units FROM batches WHERE location_id = %s", (location_id,)
        ).fetchone()["units"]
        if in_stock:
            raise HTTPException(status_code=400, detail="A location that still holds stock cannot be deactivated")
        others = conn.execute(
            "SELECT COUNT(*) AS n FROM locations WHERE is_active AND id <> %s", (location_id,)
        ).fetchone()["n"]
        if not others:
            raise HTTPException(status_code=400, detail="At least one active location is required")

    row = conn.execute(
        """
        UPDATE locations SET name = %s, location_type = %s, parent_id = %s, is_active = %s
        WHERE id = %s RETURNING *
        """,
        (body.name, body.location_type, body.parent_id, body.is_active, location_id),
    ).fetchone()
    before, after = audit.changed_fields(dict(old), dict(row))
    if after:
        audit.record(conn, user, "UPDATE", "location", location_id, before, after)
    conn.commit()
    return row
