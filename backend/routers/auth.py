# ============================================================
# AUTHENTICATION
# ============================================================

from datetime import datetime, timedelta

import psycopg
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import audit
from ..config import settings
from ..database import get_db, set_organization
from ..permissions import ROLE_LABELS, permissions_for
from ..services import delivery, plans
from ..security import (
    SESSION_COOKIE, CurrentUser, client_ip, create_reset_token, create_session, get_current_user,
    hash_password, revoke_all_sessions, revoke_session, token_hash, validate_password_strength,
    verify_password, verify_password_or_dummy,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)
    # Optional device / app name shown in the signed-in devices list.
    client_name: str | None = Field(default=None, max_length=100)


class ForgotPasswordRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)


def _user_payload(user: CurrentUser) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "role": user.role,
        "role_label": ROLE_LABELS[user.role],
        "must_change_password": user.must_change_password,
        "permissions": sorted(user.permissions),
        "organization": {
            "id": user.organization_id, "name": user.organization_name, "type": user.org_type,
            "plan": user.plan, "features": sorted(user.features),
        },
        "is_platform_admin": user.is_platform_admin,
        "location_id": user.location_id,
    }


@router.post("/login")
def login(body: LoginRequest, request: Request, conn: psycopg.Connection = Depends(get_db)):
    # Returns responses instead of raising, so the failed-attempt counter
    # is committed even when login fails.
    ip = client_ip(request)
    failure = JSONResponse(status_code=401, content={"detail": "Invalid username or password"})

    user = conn.execute(
        """
        SELECT users.id, username, full_name, role, password_hash, is_active, must_change_password,
               failed_login_count, locked_until, organization_id, is_platform_admin, location_id,
               organizations.name AS organization_name, organizations.org_type, organizations.plan,
               organizations.limits, organizations.status AS organization_status
        FROM users JOIN organizations ON organizations.id = users.organization_id
        WHERE lower(username) = lower(%s)
        FOR UPDATE OF users
        """,
        (body.username.strip(),),
    ).fetchone()

    password_ok = verify_password_or_dummy(body.password, user["password_hash"] if user else None)

    if user is None:
        audit.record(conn, None, "LOGIN_FAILED", "user", None, None,
                     {"username": body.username.strip()[:50], "reason": "unknown user"},
                     username=body.username.strip()[:50], ip=ip)
        conn.commit()
        return failure

    # Audit entries for this sign-in belong to the user's organization.
    set_organization(conn, user["organization_id"])

    if user["locked_until"] and user["locked_until"] > datetime.now():
        audit.record(conn, None, "LOGIN_BLOCKED", "user", user["id"], None,
                     {"reason": "account locked"}, username=user["username"], ip=ip)
        conn.commit()
        return JSONResponse(
            status_code=423,
            content={"detail": "Account temporarily locked after repeated failed sign-ins. Try again later."},
        )

    if password_ok and user["organization_status"] in ("SUSPENDED", "CANCELLED"):
        audit.record(conn, None, "LOGIN_BLOCKED", "user", user["id"], None,
                     {"reason": "organization not active"}, username=user["username"], ip=ip)
        conn.commit()
        return JSONResponse(status_code=403, content={"detail": "Your organization's PharmaStock account is not active."})

    if not password_ok or not user["is_active"]:
        attempts = user["failed_login_count"] + 1
        locked_until = None
        if attempts >= settings.max_failed_logins:
            locked_until = datetime.now() + timedelta(minutes=settings.lockout_minutes)
            attempts = 0
        conn.execute(
            "UPDATE users SET failed_login_count = %s, locked_until = %s WHERE id = %s",
            (attempts, locked_until, user["id"]),
        )
        audit.record(conn, None, "LOGIN_FAILED", "user", user["id"], None,
                     {"reason": "inactive account" if password_ok else "wrong password",
                      "locked": locked_until is not None},
                     username=user["username"], ip=ip)
        conn.commit()
        return failure

    conn.execute(
        """
        UPDATE users SET failed_login_count = 0, locked_until = NULL, last_login_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (user["id"],),
    )
    token, expires_at = create_session(conn, user["id"], request, (body.client_name or "").strip() or None)
    audit.record(conn, None, "LOGIN", "user", user["id"], None, None, username=user["username"], ip=ip)
    conn.commit()

    current = CurrentUser(
        id=user["id"], username=user["username"], full_name=user["full_name"], role=user["role"],
        must_change_password=user["must_change_password"], token=token, ip=ip,
        permissions=permissions_for(user["role"]),
        organization_id=user["organization_id"], organization_name=user["organization_name"],
        org_type=user["org_type"], plan=user["plan"], is_platform_admin=user["is_platform_admin"],
        location_id=user["location_id"],
        features=set(plans.effective({"plan": user["plan"], "limits": user["limits"]})["features"]),
    )

    response = JSONResponse(content={
        "token": token,
        "token_type": "bearer",
        "expires_at": expires_at.isoformat(),
        "user": _user_payload(current),
    })
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=settings.session_hours * 3600,
        httponly=True, samesite="strict", secure=settings.cookie_secure, path="/",
    )
    return response


@router.post("/logout")
def logout(response: Response, user: CurrentUser = Depends(get_current_user),
           conn: psycopg.Connection = Depends(get_db)):
    revoke_session(conn, user.token)
    audit.record(conn, user, "LOGOUT", "user", user.id)
    conn.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"message": "Signed out"}


@router.get("/me")
def me(user: CurrentUser = Depends(get_current_user)):
    return _user_payload(user)


@router.post("/change-password")
def change_password(body: ChangePasswordRequest, user: CurrentUser = Depends(get_current_user),
                    conn: psycopg.Connection = Depends(get_db)):
    row = conn.execute("SELECT password_hash FROM users WHERE id = %s FOR UPDATE", (user.id,)).fetchone()

    if not verify_password(body.current_password, row["password_hash"]):
        return JSONResponse(status_code=400, content={"detail": "Current password is incorrect"})
    if body.new_password == body.current_password:
        return JSONResponse(status_code=400, content={"detail": "New password must be different"})

    validate_password_strength(body.new_password, user.username)

    conn.execute(
        """
        UPDATE users SET password_hash = %s, must_change_password = false,
               password_changed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (hash_password(body.new_password), user.id),
    )
    # Sign out every other session of this user.
    revoke_all_sessions(conn, user.id, except_token=user.token)
    audit.record(conn, user, "PASSWORD_CHANGED", "user", user.id)
    conn.commit()
    return {"message": "Password changed"}


# ------------------------------------------------------------
# Signed-in devices
# ------------------------------------------------------------

@router.get("/sessions")
def my_sessions(user: CurrentUser = Depends(get_current_user), conn: psycopg.Connection = Depends(get_db)):
    rows = conn.execute(
        """
        SELECT id, created_at, last_seen_at, expires_at, ip_address, user_agent, client_name,
               token_hash = %s AS current
        FROM sessions
        WHERE user_id = %s AND revoked_at IS NULL AND expires_at > CURRENT_TIMESTAMP
        ORDER BY last_seen_at DESC
        """,
        (token_hash(user.token), user.id),
    ).fetchall()
    return rows


@router.delete("/sessions/{session_id}")
def revoke_my_session(session_id: int, user: CurrentUser = Depends(get_current_user),
                      conn: psycopg.Connection = Depends(get_db)):
    row = conn.execute(
        "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP WHERE id = %s AND user_id = %s AND revoked_at IS NULL "
        "RETURNING id, client_name, ip_address",
        (session_id, user.id),
    ).fetchone()
    if row is None:
        return JSONResponse(status_code=404, content={"detail": "Session not found"})
    audit.record(conn, user, "REVOKE_SESSION", "user", user.id, None, dict(row))
    conn.commit()
    return {"revoked": session_id}


@router.post("/logout-others")
def logout_other_sessions(user: CurrentUser = Depends(get_current_user),
                          conn: psycopg.Connection = Depends(get_db)):
    revoke_all_sessions(conn, user.id, except_token=user.token)
    audit.record(conn, user, "LOGOUT_OTHER_SESSIONS", "user", user.id)
    conn.commit()
    return {"message": "All other devices were signed out"}


# ------------------------------------------------------------
# Password reset
# ------------------------------------------------------------

RESET_HOURS = 1


@router.post("/forgot-password", status_code=202)
def forgot_password(body: ForgotPasswordRequest, request: Request, conn: psycopg.Connection = Depends(get_db)):
    """E-mail a one-time reset link to the user's address, if they have one.
    The response is identical whether or not the account exists."""
    generic = {"message": "If the account exists and has an e-mail address, a reset link has been sent. "
                          "Otherwise ask your administrator to reset your password."}
    user = conn.execute(
        """
        SELECT users.id, users.username, users.email, users.is_active, users.organization_id,
               organizations.status
        FROM users JOIN organizations ON organizations.id = users.organization_id
        WHERE lower(username) = lower(%s)
        """,
        (body.username.strip(),),
    ).fetchone()
    if user is None or not user["is_active"] or not user["email"] or user["status"] in ("SUSPENDED", "CANCELLED"):
        return generic
    set_organization(conn, user["organization_id"])
    token, expires_at = create_reset_token(conn, user["id"], "FORGOT", None, RESET_HOURS)
    base = settings.app_base_url or str(request.base_url).rstrip("/")
    delivery.enqueue(
        conn, channel="EMAIL", recipient=user["email"], user_id=user["id"],
        subject="PharmaStock password reset",
        body=(f"A password reset was requested for your PharmaStock account '{user['username']}'.\n\n"
              f"Open this link within {RESET_HOURS} hour to choose a new password:\n"
              f"{base}/app/#/reset-password?token={token}\n\n"
              "If you did not ask for this, ignore this e-mail; your password is unchanged."),
    )
    audit.record(conn, None, "PASSWORD_RESET_REQUESTED", "user", user["id"], None,
                 {"expires_at": expires_at}, username=user["username"], ip=client_ip(request))
    conn.commit()
    return generic


@router.post("/reset-password")
def reset_password(body: ResetPasswordRequest, request: Request, conn: psycopg.Connection = Depends(get_db)):
    row = conn.execute(
        """
        SELECT t.id, t.user_id, t.expires_at, t.used_at, users.username, users.organization_id, users.is_active
        FROM password_reset_tokens t JOIN users ON users.id = t.user_id
        WHERE t.token_hash = %s
        FOR UPDATE OF t
        """,
        (token_hash(body.token),),
    ).fetchone()
    if row is None or row["used_at"] or row["expires_at"] <= datetime.now() or not row["is_active"]:
        return JSONResponse(status_code=400, content={"detail": "This reset link is invalid or has expired. "
                                                                "Request a new one."})
    validate_password_strength(body.new_password, row["username"])
    set_organization(conn, row["organization_id"])
    conn.execute(
        """
        UPDATE users SET password_hash = %s, must_change_password = false, failed_login_count = 0,
               locked_until = NULL, password_changed_at = CURRENT_TIMESTAMP, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (hash_password(body.new_password), row["user_id"]),
    )
    conn.execute("UPDATE password_reset_tokens SET used_at = CURRENT_TIMESTAMP WHERE id = %s", (row["id"],))
    revoke_all_sessions(conn, row["user_id"])
    audit.record(conn, None, "PASSWORD_RESET_COMPLETED", "user", row["user_id"], None, None,
                 username=row["username"], ip=client_ip(request))
    conn.commit()
    return {"message": "Password changed. You can now sign in."}
