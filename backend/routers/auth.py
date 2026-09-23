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
from ..database import get_db
from ..permissions import ROLE_LABELS, permissions_for
from ..security import (
    SESSION_COOKIE, CurrentUser, client_ip, create_session, get_current_user, hash_password,
    revoke_all_sessions, revoke_session, validate_password_strength, verify_password,
    verify_password_or_dummy,
)

router = APIRouter(prefix="/auth", tags=["Authentication"])


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


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
    }


@router.post("/login")
def login(body: LoginRequest, request: Request, conn: psycopg.Connection = Depends(get_db)):
    # Returns responses instead of raising, so the failed-attempt counter
    # is committed even when login fails.
    ip = client_ip(request)
    failure = JSONResponse(status_code=401, content={"detail": "Invalid username or password"})

    user = conn.execute(
        """
        SELECT id, username, full_name, role, password_hash, is_active, must_change_password,
               failed_login_count, locked_until
        FROM users WHERE lower(username) = lower(%s)
        FOR UPDATE
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

    if user["locked_until"] and user["locked_until"] > datetime.now():
        audit.record(conn, None, "LOGIN_BLOCKED", "user", user["id"], None,
                     {"reason": "account locked"}, username=user["username"], ip=ip)
        conn.commit()
        return JSONResponse(
            status_code=423,
            content={"detail": "Account temporarily locked after repeated failed sign-ins. Try again later."},
        )

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
    token, expires_at = create_session(conn, user["id"], request)
    audit.record(conn, None, "LOGIN", "user", user["id"], None, None, username=user["username"], ip=ip)
    conn.commit()

    current = CurrentUser(
        id=user["id"], username=user["username"], full_name=user["full_name"], role=user["role"],
        must_change_password=user["must_change_password"], token=token, ip=ip,
        permissions=permissions_for(user["role"]),
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
