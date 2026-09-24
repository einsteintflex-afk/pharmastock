# ============================================================
# USER ACCOUNTS
# ============================================================

import secrets
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .. import audit
from ..config import settings
from ..database import get_db
from ..permissions import PERMISSIONS, ROLE_LABELS, ROLE_PERMISSIONS, ROLES
from ..schemas import Email, Name150, blank_to_none
from ..security import (
    CurrentUser, create_reset_token, hash_password, require, revoke_all_sessions, validate_password_strength,
)
from ..services import delivery, plans

router = APIRouter(tags=["Users"])

RoleName = Literal["OWNER", "ADMINISTRATOR", "MANAGER", "PHARMACIST", "PHARMACY_TECHNICIAN", "STOREKEEPER",
                   "VIEWER", "INVENTORY_OFFICER", "PURCHASING_OFFICER", "CASHIER", "AUDITOR"]
# Roles that count as "an administrator" for the last-administrator rule.
_ADMIN = ("OWNER", "ADMINISTRATOR")

USER_COLUMNS = """
    id, username, full_name, email, role, is_active, must_change_password,
    last_login_at, created_at, updated_at, locked_until, location_id, is_platform_admin, mfa_enabled
"""


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9._-]{3,50}$")
    full_name: Name150
    email: Email | None = None
    role: RoleName
    password: str = Field(min_length=1, max_length=200)
    location_id: int | None = None


class UserUpdate(BaseModel):
    full_name: Name150
    email: Email | None = None
    role: RoleName
    is_active: bool
    location_id: int | None = None


class PasswordReset(BaseModel):
    temporary_password: str | None = Field(default=None, max_length=200)


# The users table is not under row level security (sign-in must find a
# user before the organization is known), so every query here filters on
# the caller's organization explicitly.

def _get(conn, user_id: int, organization_id: int) -> dict:
    row = conn.execute(
        f"SELECT {USER_COLUMNS} FROM users WHERE id = %s AND organization_id = %s", (user_id, organization_id)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return row


def _active_admins(conn, organization_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = ANY(%s) AND is_active AND organization_id = %s",
        (list(_ADMIN), organization_id),
    ).fetchone()["n"]


def _check_owner_rule(user: CurrentUser, *roles: str) -> None:
    """Only an owner can create, promote to, change or demote an owner:
    an administrator cannot take over the organization's billing."""
    if "OWNER" in roles and user.role != "OWNER":
        raise HTTPException(status_code=403, detail="Only an organization owner can assign or change the owner role.")


def _check_location(conn, location_id: int | None) -> None:
    # locations is under row level security: only this organization's rows are visible.
    if location_id is not None and conn.execute(
        "SELECT 1 FROM locations WHERE id = %s", (location_id,)
    ).fetchone() is None:
        raise HTTPException(status_code=404, detail="Location not found")


@router.get("/roles")
def list_roles(user: CurrentUser = Depends(require("inventory.read"))):
    return {
        "roles": [
            {"role": role, "label": ROLE_LABELS[role], "permissions": sorted(ROLE_PERMISSIONS[role])}
            for role in ROLES
        ],
        "permissions": PERMISSIONS,
    }


@router.get("/users")
def list_users(user: CurrentUser = Depends(require("users.manage")),
               conn: psycopg.Connection = Depends(get_db)):
    rows = conn.execute(
        f"SELECT {USER_COLUMNS} FROM users WHERE organization_id = %s ORDER BY lower(username)",
        (user.organization_id,),
    ).fetchall()
    return [{**row, "role_label": ROLE_LABELS[row["role"]]} for row in rows]


@router.post("/users", status_code=201)
def create_user(body: UserCreate, user: CurrentUser = Depends(require("users.manage")),
                conn: psycopg.Connection = Depends(get_db)):
    validate_password_strength(body.password, body.username)
    _check_owner_rule(user, body.role)
    _check_location(conn, body.location_id)
    active = conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE organization_id = %s AND is_active", (user.organization_id,)
    ).fetchone()["n"]
    plans.check_limit(conn, user.organization_id, "max_users", active)

    exists = conn.execute(
        "SELECT 1 FROM users WHERE lower(username) = lower(%s)", (body.username,)
    ).fetchone()
    if exists:
        raise HTTPException(status_code=409, detail="Username already exists")

    row = conn.execute(
        f"""
        INSERT INTO users (username, full_name, email, role, password_hash, must_change_password,
                           organization_id, location_id)
        VALUES (%s, %s, %s, %s, %s, true, %s, %s)
        RETURNING {USER_COLUMNS}
        """,
        (body.username, body.full_name, blank_to_none(body.email), body.role, hash_password(body.password),
         user.organization_id, body.location_id),
    ).fetchone()
    audit.record(conn, user, "CREATE", "user", row["id"], None,
                 {"username": row["username"], "full_name": row["full_name"], "role": row["role"]})
    conn.commit()
    return row


@router.put("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, user: CurrentUser = Depends(require("users.manage")),
                conn: psycopg.Connection = Depends(get_db)):
    old = _get(conn, user_id, user.organization_id)
    if body.role != old["role"] or not body.is_active:
        _check_owner_rule(user, body.role, old["role"])
    _check_location(conn, body.location_id)

    if user_id == user.id and (not body.is_active or body.role != old["role"]):
        raise HTTPException(status_code=400, detail="You cannot deactivate yourself or change your own role")

    removing_admin = old["role"] in _ADMIN and old["is_active"] and (
        body.role not in _ADMIN or not body.is_active
    )
    if removing_admin and _active_admins(conn, user.organization_id) <= 1:
        raise HTTPException(status_code=400, detail="At least one active administrator is required")

    row = conn.execute(
        f"""
        UPDATE users SET full_name = %s, email = %s, role = %s, is_active = %s, location_id = %s,
               updated_at = CURRENT_TIMESTAMP
        WHERE id = %s AND organization_id = %s
        RETURNING {USER_COLUMNS}
        """,
        (body.full_name, blank_to_none(body.email), body.role, body.is_active, body.location_id,
         user_id, user.organization_id),
    ).fetchone()

    if not body.is_active or body.role != old["role"]:
        revoke_all_sessions(conn, user_id)

    fields = ["full_name", "email", "role", "is_active", "location_id"]
    before, after = audit.changed_fields({k: old[k] for k in fields}, {k: row[k] for k in fields})
    audit.record(conn, user, "UPDATE", "user", user_id, before, after)
    conn.commit()
    return row


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, body: PasswordReset, user: CurrentUser = Depends(require("users.manage")),
                   conn: psycopg.Connection = Depends(get_db)):
    target = _get(conn, user_id, user.organization_id)
    _check_owner_rule(user, target["role"])
    temporary = body.temporary_password or (secrets.token_urlsafe(9) + "7a")
    validate_password_strength(temporary, target["username"])

    conn.execute(
        """
        UPDATE users SET password_hash = %s, must_change_password = true, failed_login_count = 0,
               locked_until = NULL, password_changed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s AND organization_id = %s
        """,
        (hash_password(temporary), user_id, user.organization_id),
    )
    revoke_all_sessions(conn, user_id)
    audit.record(conn, user, "PASSWORD_RESET", "user", user_id)
    conn.commit()
    # Shown once to the administrator; the user must change it at next sign-in.
    return {"message": "Password reset", "temporary_password": temporary}


class ResetLinkRequest(BaseModel):
    send_email: bool = True


@router.post("/users/{user_id}/reset-link")
def create_reset_link(user_id: int, body: ResetLinkRequest, request: Request,
                      user: CurrentUser = Depends(require("users.manage")),
                      conn: psycopg.Connection = Depends(get_db)):
    """One-time link (24 hours) with which the user sets their own password;
    the administrator never learns it. E-mailed when the user has an address."""
    target = _get(conn, user_id, user.organization_id)
    _check_owner_rule(user, target["role"])
    token, expires_at = create_reset_token(conn, user_id, "ADMIN_RESET", user.id, 24)
    base = settings.app_base_url or str(request.base_url).rstrip("/")
    link = f"{base}/app/#/reset-password?token={token}"
    emailed = bool(body.send_email and target["email"])
    if emailed:
        delivery.enqueue(conn, channel="EMAIL", recipient=target["email"], user_id=user_id,
                         subject="Set your PharmaStock password",
                         body=(f"{user.full_name} created a link for you to set a new PharmaStock password "
                               f"for the account '{target['username']}'.\n\nOpen within 24 hours:\n{link}"))
    audit.record(conn, user, "PASSWORD_RESET_LINK", "user", user_id, None,
                 {"expires_at": expires_at, "emailed": emailed})
    conn.commit()
    return {"link": link, "expires_at": expires_at, "emailed": emailed}


@router.get("/users/{user_id}/sessions")
def user_sessions(user_id: int, user: CurrentUser = Depends(require("users.manage")),
                  conn: psycopg.Connection = Depends(get_db)):
    _get(conn, user_id, user.organization_id)
    return conn.execute(
        """
        SELECT id, created_at, last_seen_at, expires_at, ip_address, user_agent, client_name
        FROM sessions WHERE user_id = %s AND revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP
        ORDER BY last_seen_at DESC
        """,
        (user_id,),
    ).fetchall()


@router.post("/users/{user_id}/revoke-sessions")
def revoke_user_sessions(user_id: int, user: CurrentUser = Depends(require("users.manage")),
                         conn: psycopg.Connection = Depends(get_db)):
    _get(conn, user_id, user.organization_id)
    if user_id == user.id:
        revoke_all_sessions(conn, user_id, except_token=user.token)
    else:
        revoke_all_sessions(conn, user_id)
    audit.record(conn, user, "REVOKE_SESSIONS", "user", user_id)
    conn.commit()
    return {"message": "Sessions revoked"}


@router.post("/users/{user_id}/reset-mfa")
def reset_user_mfa(user_id: int, user: CurrentUser = Depends(require("users.manage")),
                   conn: psycopg.Connection = Depends(get_db)):
    """For a user who lost their authenticator and recovery codes: MFA is
    switched off and their sessions end; they must set it up again."""
    target = _get(conn, user_id, user.organization_id)
    _check_owner_rule(user, target["role"])
    if user_id == user.id:
        raise HTTPException(status_code=400, detail="Use your own security settings to change your MFA")
    conn.execute(
        """
        UPDATE users SET mfa_enabled = false, mfa_secret_encrypted = NULL, mfa_recovery_codes = NULL,
               mfa_enabled_at = NULL, mfa_last_counter = NULL, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s AND organization_id = %s
        """,
        (user_id, user.organization_id),
    )
    revoke_all_sessions(conn, user_id)
    audit.record(conn, user, "MFA_RESET", "user", user_id)
    conn.commit()
    return {"message": "Two-step verification was reset; the user must set it up again"}
