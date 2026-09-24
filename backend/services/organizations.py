# ============================================================
# ORGANIZATIONS (TENANTS)
# ============================================================

import psycopg
from fastapi import HTTPException

from ..database import set_organization
from . import app_settings, plans

FIRST_LOCATION = {
    "HOSPITAL": ("Central Store", "CENTRAL_STORE"),
    "WHOLESALE": ("Main Warehouse", "STORE"),
}


def usage(conn: psycopg.Connection, organization_id: int) -> dict:
    users = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE organization_id = %s AND is_active", (organization_id,)
    ).fetchone()["n"]
    # locations/medicines are under row level security: counts are for the
    # organization currently set on the connection.
    locations = conn.execute("SELECT COUNT(*) AS n FROM locations WHERE is_active").fetchone()["n"]
    medicines = conn.execute("SELECT COUNT(*) AS n FROM medicines").fetchone()["n"]
    return {"active_users": users, "active_locations": locations, "medicines": medicines}


def describe(conn: psycopg.Connection, organization_id: int) -> dict:
    org = plans.organization(conn, organization_id)
    return {
        "id": org["id"], "name": org["name"], "org_type": org["org_type"], "status": org["status"],
        "trial_ends_at": org["trial_ends_at"], "current_period_end": org["current_period_end"],
        "created_at": org["created_at"],
        **plans.effective(org),
        "feature_labels": plans.FEATURE_LABELS,
        "usage": usage(conn, organization_id),
    }


def create(conn: psycopg.Connection, *, name: str, org_type: str, plan: str, status: str,
           admin_username: str, admin_full_name: str, admin_password_hash: str,
           return_to_organization: int | None) -> dict:
    """Create an organization with its settings, first location and first
    administrator. Runs in the caller's transaction."""
    if conn.execute("SELECT 1 FROM users WHERE lower(username) = lower(%s)", (admin_username,)).fetchone():
        raise HTTPException(status_code=409, detail="Username already exists")

    org = conn.execute(
        "INSERT INTO organizations (name, org_type, plan, status) VALUES (%s, %s, %s, %s) RETURNING *",
        (name, org_type, plan, status),
    ).fetchone()

    # Seed the new organization's own rows under its own tenant context.
    set_organization(conn, org["id"])
    app_settings.seed_defaults(conn, {"pharmacy.name": name})
    location_name, location_type = FIRST_LOCATION.get(org_type, ("Main Pharmacy", "PHARMACY"))
    conn.execute("INSERT INTO locations (name, location_type) VALUES (%s, %s)", (location_name, location_type))
    admin = conn.execute(
        """
        INSERT INTO users (username, full_name, role, password_hash, must_change_password, organization_id)
        VALUES (%s, %s, 'OWNER', %s, true, %s)
        RETURNING id, username
        """,
        (admin_username, admin_full_name, admin_password_hash, org["id"]),
    ).fetchone()
    conn.execute(
        """
        INSERT INTO audit_log (username, action, entity_type, entity_id, new_value)
        VALUES ('system', 'CREATE', 'organization', %s, jsonb_build_object('name', %s::text, 'plan', %s::text))
        """,
        (str(org["id"]), name, plan),
    )
    if return_to_organization is not None:
        set_organization(conn, return_to_organization)
    return {"organization": org, "administrator": admin}
