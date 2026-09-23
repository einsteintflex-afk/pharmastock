# ============================================================
# USER ACCOUNTS
# ============================================================

import secrets
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import audit
from ..database import get_db
from ..permissions import PERMISSIONS, ROLE_LABELS, ROLE_PERMISSIONS, ROLES
from ..schemas import Email, Name150, blank_to_none
from ..security import CurrentUser, hash_password, require, revoke_all_sessions, validate_password_strength

router = APIRouter(tags=["Users"])

RoleName = Literal["ADMINISTRATOR", "MANAGER", "PHARMACIST", "PHARMACY_TECHNICIAN", "STOREKEEPER", "VIEWER"]

USER_COLUMNS = """
    id, username, full_name, email, role, is_active, must_change_password,
    last_login_at, created_at, updated_at, locked_until
"""


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z0-9._-]{3,50}$")
    full_name: Name150
    email: Email | None = None
    role: RoleName
    password: str = Field(min_length=1, max_length=200)


class UserUpdate(BaseModel):
    full_name: Name150
    email: Email | None = None
    role: RoleName
    is_active: bool


class PasswordReset(BaseModel):
    temporary_password: str | None = Field(default=None, max_length=200)


def _get(conn, user_id: int) -> dict:
    row = conn.execute(f"SELECT {USER_COLUMNS} FROM users WHERE id = %s", (user_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="User not found")
    return row


def _active_admins(conn) -> int:
    return conn.execute(
        "SELECT COUNT(*) AS n FROM users WHERE role = 'ADMINISTRATOR' AND is_active"
    ).fetchone()["n"]


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
    rows = conn.execute(f"SELECT {USER_COLUMNS} FROM users ORDER BY lower(username)").fetchall()
    return [{**row, "role_label": ROLE_LABELS[row["role"]]} for row in rows]


@router.post("/users", status_code=201)
def create_user(body: UserCreate, user: CurrentUser = Depends(require("users.manage")),
                conn: psycopg.Connection = Depends(get_db)):
    validate_password_strength(body.password, body.username)

    exists = conn.execute(
        "SELECT 1 FROM users WHERE lower(username) = lower(%s)", (body.username,)
    ).fetchone()
    if exists:
        raise HTTPException(status_code=409, detail="Username already exists")

    row = conn.execute(
        f"""
        INSERT INTO users (username, full_name, email, role, password_hash, must_change_password)
        VALUES (%s, %s, %s, %s, %s, true)
        RETURNING {USER_COLUMNS}
        """,
        (body.username, body.full_name, blank_to_none(body.email), body.role, hash_password(body.password)),
    ).fetchone()
    audit.record(conn, user, "CREATE", "user", row["id"], None,
                 {"username": row["username"], "full_name": row["full_name"], "role": row["role"]})
    conn.commit()
    return row


@router.put("/users/{user_id}")
def update_user(user_id: int, body: UserUpdate, user: CurrentUser = Depends(require("users.manage")),
                conn: psycopg.Connection = Depends(get_db)):
    old = _get(conn, user_id)

    if user_id == user.id and (not body.is_active or body.role != old["role"]):
        raise HTTPException(status_code=400, detail="You cannot deactivate yourself or change your own role")

    removing_admin = old["role"] == "ADMINISTRATOR" and old["is_active"] and (
        body.role != "ADMINISTRATOR" or not body.is_active
    )
    if removing_admin and _active_admins(conn) <= 1:
        raise HTTPException(status_code=400, detail="At least one active administrator is required")

    row = conn.execute(
        f"""
        UPDATE users SET full_name = %s, email = %s, role = %s, is_active = %s,
               updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        RETURNING {USER_COLUMNS}
        """,
        (body.full_name, blank_to_none(body.email), body.role, body.is_active, user_id),
    ).fetchone()

    if not body.is_active or body.role != old["role"]:
        revoke_all_sessions(conn, user_id)

    fields = ["full_name", "email", "role", "is_active"]
    before, after = audit.changed_fields({k: old[k] for k in fields}, {k: row[k] for k in fields})
    audit.record(conn, user, "UPDATE", "user", user_id, before, after)
    conn.commit()
    return row


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, body: PasswordReset, user: CurrentUser = Depends(require("users.manage")),
                   conn: psycopg.Connection = Depends(get_db)):
    target = _get(conn, user_id)
    temporary = body.temporary_password or (secrets.token_urlsafe(9) + "7a")
    validate_password_strength(temporary, target["username"])

    conn.execute(
        """
        UPDATE users SET password_hash = %s, must_change_password = true, failed_login_count = 0,
               locked_until = NULL, password_changed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (hash_password(temporary), user_id),
    )
    revoke_all_sessions(conn, user_id)
    audit.record(conn, user, "PASSWORD_RESET", "user", user_id)
    conn.commit()
    # Shown once to the administrator; the user must change it at next sign-in.
    return {"message": "Password reset", "temporary_password": temporary}
