# ============================================================
# AUTHENTICATION
# ============================================================

import secrets
from datetime import datetime, timedelta

import psycopg
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import audit, crypto, security_events
from ..config import settings
from ..database import get_db, set_organization
from ..permissions import ROLE_LABELS, permissions_for
from ..services import delivery, plans
from ..security import (
    SESSION_COOKIE, CurrentUser, client_ip, create_reset_token, create_session, get_current_user,
    hash_password, mfa_required_for, revoke_all_sessions, revoke_session, session_hours, token_hash,
    validate_password_strength, verify_password, verify_password_or_dummy,
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
        "mfa_enabled": user.mfa_enabled,
        "mfa_setup_required": user.mfa_setup_required,
        # The platform console needs a second factor in this session.
        "platform_access": user.is_platform_admin and user.mfa_enabled and user.mfa_verified_at is not None,
    }


_LOGIN_USER_SELECT = """
        SELECT users.id, username, full_name, role, password_hash, is_active, must_change_password,
               failed_login_count, locked_until, organization_id, is_platform_admin, location_id, mfa_enabled,
               organizations.name AS organization_name, organizations.org_type, organizations.plan,
               organizations.limits, organizations.status AS organization_status
        FROM users JOIN organizations ON organizations.id = users.organization_id"""


@router.post("/login")
def login(body: LoginRequest, request: Request, conn: psycopg.Connection = Depends(get_db)):
    # Returns responses instead of raising, so the failed-attempt counter
    # is committed even when login fails.
    ip = client_ip(request)
    failure = JSONResponse(status_code=401, content={"detail": "Invalid username or password"})

    user = conn.execute(
        f"""
        {_LOGIN_USER_SELECT}
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

    if user["mfa_enabled"]:
        # Password correct: a second factor is still needed. The failed-sign-in
        # counter is reset only when the whole sign-in succeeds.
        challenge = secrets.token_urlsafe(32)
        conn.execute(
            """
            INSERT INTO mfa_challenges (user_id, token_hash, expires_at, client_name, ip_address)
            VALUES (%s, %s, CURRENT_TIMESTAMP + interval '5 minutes', %s, %s)
            """,
            (user["id"], token_hash(challenge), (body.client_name or "").strip() or None, ip),
        )
        audit.record(conn, None, "LOGIN_PASSWORD_OK_MFA_PENDING", "user", user["id"], None, None,
                     username=user["username"], ip=ip)
        conn.commit()
        return JSONResponse(content={"mfa_required": True, "challenge_token": challenge,
                                     "methods": ["totp", "recovery_code"]})

    return _complete_login(conn, request, user, body.client_name, mfa_verified=False)


def _complete_login(conn, request: Request, user: dict, client_name: str | None, *, mfa_verified: bool):
    ip = client_ip(request)
    conn.execute(
        """
        UPDATE users SET failed_login_count = 0, locked_until = NULL, last_login_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (user["id"],),
    )
    privileged = bool(user["is_platform_admin"])
    token, expires_at = create_session(conn, user["id"], request, (client_name or "").strip() or None,
                                       privileged=privileged, mfa_verified=mfa_verified)
    audit.record(conn, None, "LOGIN", "user", user["id"], None, {"mfa": mfa_verified} if mfa_verified else None,
                 username=user["username"], ip=ip)
    if privileged:
        audit.platform(conn, None, "PLATFORM_ADMIN_LOGIN", user["organization_id"], "user", user["id"],
                       {"username": user["username"], "mfa": mfa_verified})
    conn.commit()

    current = CurrentUser(
        id=user["id"], username=user["username"], full_name=user["full_name"], role=user["role"],
        must_change_password=user["must_change_password"], token=token, ip=ip,
        permissions=permissions_for(user["role"]),
        organization_id=user["organization_id"], organization_name=user["organization_name"],
        org_type=user["org_type"], plan=user["plan"], is_platform_admin=user["is_platform_admin"],
        location_id=user["location_id"],
        features=set(plans.effective(conn, {"plan": user["plan"], "limits": user["limits"]})["features"]),
        mfa_enabled=user["mfa_enabled"], mfa_verified_at=datetime.now() if mfa_verified else None,
    )
    current.mfa_setup_required = mfa_required_for(conn, current) and not current.mfa_enabled

    response = JSONResponse(content={
        "token": token,
        "token_type": "bearer",
        "expires_at": expires_at.isoformat(),
        "user": _user_payload(current),
    })
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=session_hours(privileged) * 3600,
        httponly=True, samesite="strict", secure=settings.cookie_secure, path="/",
    )
    return response


def _check_second_factor(conn, user_id: int, code: str) -> str | None:
    """Verify a TOTP code or a one-time recovery code for a user with MFA.
    Returns "totp" / "recovery_code", or None. Must run in a transaction that
    holds the user row lock (replay protection)."""
    row = conn.execute(
        "SELECT mfa_secret_encrypted, mfa_last_counter, mfa_recovery_codes FROM users WHERE id = %s FOR UPDATE",
        (user_id,),
    ).fetchone()
    if not row or not row["mfa_secret_encrypted"]:
        return None
    code = (code or "").strip()
    counter = crypto.verify_totp(crypto.decrypt(row["mfa_secret_encrypted"]), code, row["mfa_last_counter"])
    if counter is not None:
        conn.execute("UPDATE users SET mfa_last_counter = %s WHERE id = %s", (counter, user_id))
        return "totp"
    hashed = crypto.hash_code(code)
    if code and hashed in (row["mfa_recovery_codes"] or []):
        conn.execute("UPDATE users SET mfa_recovery_codes = array_remove(mfa_recovery_codes, %s) WHERE id = %s",
                     (hashed, user_id))
        return "recovery_code"
    return None


class MfaVerifyRequest(BaseModel):
    challenge_token: str = Field(min_length=20, max_length=200)
    code: str = Field(min_length=6, max_length=20)


MAX_MFA_ATTEMPTS = 5


@router.post("/mfa/verify")
def mfa_verify(body: MfaVerifyRequest, request: Request, conn: psycopg.Connection = Depends(get_db)):
    """Second step of sign-in: the code from the authenticator app (or a
    recovery code) for the challenge issued by /auth/login."""
    ip = client_ip(request)
    invalid = JSONResponse(status_code=401, content={"detail": "Sign-in expired. Enter your password again."})
    challenge = conn.execute(
        """
        SELECT id, user_id, expires_at, attempts, used_at, client_name
        FROM mfa_challenges WHERE token_hash = %s FOR UPDATE
        """,
        (token_hash(body.challenge_token),),
    ).fetchone()
    if (challenge is None or challenge["used_at"] or challenge["expires_at"] <= datetime.now()
            or challenge["attempts"] >= MAX_MFA_ATTEMPTS):
        return invalid

    user = conn.execute(
        f"""
        {_LOGIN_USER_SELECT}
        WHERE users.id = %s
        FOR UPDATE OF users
        """,
        (challenge["user_id"],),
    ).fetchone()
    set_organization(conn, user["organization_id"])
    if not user["is_active"] or user["organization_status"] in ("SUSPENDED", "CANCELLED") \
            or (user["locked_until"] and user["locked_until"] > datetime.now()):
        return invalid

    method = _check_second_factor(conn, user["id"], body.code)
    if method is None:
        attempts = user["failed_login_count"] + 1
        locked_until = None
        if attempts >= settings.max_failed_logins:
            locked_until = datetime.now() + timedelta(minutes=settings.lockout_minutes)
            attempts = 0
        conn.execute("UPDATE users SET failed_login_count = %s, locked_until = %s WHERE id = %s",
                     (attempts, locked_until, user["id"]))
        conn.execute("UPDATE mfa_challenges SET attempts = attempts + 1 WHERE id = %s", (challenge["id"],))
        audit.record(conn, None, "MFA_FAILED", "user", user["id"], None, {"locked": locked_until is not None},
                     username=user["username"], ip=ip)
        conn.commit()
        security_events.record("MFA_FAILED", organization_id=user["organization_id"], user_id=user["id"],
                               username=user["username"], method="POST", path="/auth/mfa/verify",
                               status_code=401, ip=ip, request_id=audit.current_request_id())
        return JSONResponse(status_code=401, content={"detail": "Incorrect code. Check the time on your phone "
                                                                "and try the current code."})

    conn.execute("UPDATE mfa_challenges SET used_at = CURRENT_TIMESTAMP WHERE id = %s", (challenge["id"],))
    if method == "recovery_code":
        audit.record(conn, None, "MFA_RECOVERY_CODE_USED", "user", user["id"], None, None,
                     username=user["username"], ip=ip)
    return _complete_login(conn, request, user, challenge["client_name"], mfa_verified=True)


# ------------------------------------------------------------
# Two-step verification (TOTP) management
# ------------------------------------------------------------

class MfaSetupRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class MfaCodeRequest(BaseModel):
    code: str = Field(min_length=6, max_length=20)


class MfaDisableRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=6, max_length=20)


@router.get("/mfa")
def mfa_status(user: CurrentUser = Depends(get_current_user), conn: psycopg.Connection = Depends(get_db)):
    row = conn.execute("SELECT mfa_enabled, mfa_enabled_at, COALESCE(cardinality(mfa_recovery_codes), 0) AS codes "
                       "FROM users WHERE id = %s", (user.id,)).fetchone()
    return {
        "enabled": row["mfa_enabled"],
        "enabled_at": row["mfa_enabled_at"],
        "recovery_codes_remaining": row["codes"] if row["mfa_enabled"] else 0,
        "required": user.is_platform_admin or mfa_required_for(conn, user),
        "session_verified_at": user.mfa_verified_at,
    }


@router.post("/mfa/setup")
def mfa_setup(body: MfaSetupRequest, user: CurrentUser = Depends(get_current_user),
              conn: psycopg.Connection = Depends(get_db)):
    """Start (or restart) set-up: a new secret, shown once as a QR code. MFA is
    not active until /auth/mfa/enable confirms a code from the app."""
    row = conn.execute("SELECT password_hash, mfa_enabled FROM users WHERE id = %s FOR UPDATE",
                       (user.id,)).fetchone()
    if not verify_password(body.password, row["password_hash"]):
        return JSONResponse(status_code=400, content={"detail": "Password is incorrect"})
    if row["mfa_enabled"]:
        return JSONResponse(status_code=409, content={"detail": "Two-step verification is already on. "
                                                                "Turn it off first to change the device."})
    secret = crypto.new_totp_secret()
    conn.execute("UPDATE users SET mfa_secret_encrypted = %s, mfa_last_counter = NULL WHERE id = %s",
                 (crypto.encrypt(secret), user.id))
    audit.record(conn, user, "MFA_SETUP_STARTED", "user", user.id)
    conn.commit()
    uri = crypto.provisioning_uri(secret, f"{user.username} ({user.organization_name})")
    return {"secret": secret, "otpauth_uri": uri, "qr_svg": _qr_svg(uri)}


def _qr_svg(uri: str) -> str:
    import io

    import segno
    out = io.BytesIO()
    segno.make(uri, error="m").save(out, kind="svg", scale=5, border=2, xmldecl=False, svgns=True)
    return out.getvalue().decode()


@router.post("/mfa/enable")
def mfa_enable(body: MfaCodeRequest, user: CurrentUser = Depends(get_current_user),
               conn: psycopg.Connection = Depends(get_db)):
    row = conn.execute("SELECT mfa_secret_encrypted, mfa_enabled FROM users WHERE id = %s FOR UPDATE",
                       (user.id,)).fetchone()
    if row["mfa_enabled"]:
        return JSONResponse(status_code=409, content={"detail": "Two-step verification is already on"})
    if not row["mfa_secret_encrypted"]:
        return JSONResponse(status_code=400, content={"detail": "Start the set-up first"})
    counter = crypto.verify_totp(crypto.decrypt(row["mfa_secret_encrypted"]), body.code, None)
    if counter is None:
        return JSONResponse(status_code=400, content={"detail": "Incorrect code. Check the time on your phone."})
    codes = crypto.new_recovery_codes()
    conn.execute(
        """
        UPDATE users SET mfa_enabled = true, mfa_enabled_at = CURRENT_TIMESTAMP, mfa_last_counter = %s,
               mfa_recovery_codes = %s WHERE id = %s
        """,
        (counter, [crypto.hash_code(c) for c in codes], user.id),
    )
    conn.execute("UPDATE sessions SET mfa_verified_at = CURRENT_TIMESTAMP WHERE id = %s", (user.session_id,))
    # Other devices signed in with the password alone are signed out.
    revoke_all_sessions(conn, user.id, except_token=user.token)
    audit.record(conn, user, "MFA_ENABLED", "user", user.id)
    if user.is_platform_admin:
        audit.platform(conn, user, "PLATFORM_ADMIN_MFA_ENABLED", user.organization_id, "user", user.id)
    conn.commit()
    # Shown once; only hashes are stored.
    return {"enabled": True, "recovery_codes": codes}


@router.post("/mfa/recovery-codes")
def mfa_new_recovery_codes(body: MfaCodeRequest, user: CurrentUser = Depends(get_current_user),
                           conn: psycopg.Connection = Depends(get_db)):
    if not user.mfa_enabled:
        return JSONResponse(status_code=400, content={"detail": "Two-step verification is not on"})
    if _check_second_factor(conn, user.id, body.code) != "totp":
        conn.commit()
        return JSONResponse(status_code=400, content={"detail": "Incorrect authenticator code"})
    codes = crypto.new_recovery_codes()
    conn.execute("UPDATE users SET mfa_recovery_codes = %s WHERE id = %s",
                 ([crypto.hash_code(c) for c in codes], user.id))
    audit.record(conn, user, "MFA_RECOVERY_CODES_REGENERATED", "user", user.id)
    conn.commit()
    return {"recovery_codes": codes}


@router.post("/mfa/disable")
def mfa_disable(body: MfaDisableRequest, user: CurrentUser = Depends(get_current_user),
                conn: psycopg.Connection = Depends(get_db)):
    if user.is_platform_admin or mfa_required_for(conn, user):
        return JSONResponse(status_code=403, content={"detail": "Two-step verification is required for your "
                                                                "role and cannot be turned off."})
    row = conn.execute("SELECT password_hash FROM users WHERE id = %s FOR UPDATE", (user.id,)).fetchone()
    if not verify_password(body.password, row["password_hash"]) or not _check_second_factor(conn, user.id, body.code):
        conn.commit()
        return JSONResponse(status_code=400, content={"detail": "Password or code is incorrect"})
    conn.execute(
        """
        UPDATE users SET mfa_enabled = false, mfa_secret_encrypted = NULL, mfa_recovery_codes = NULL,
               mfa_enabled_at = NULL, mfa_last_counter = NULL WHERE id = %s
        """,
        (user.id,),
    )
    audit.record(conn, user, "MFA_DISABLED", "user", user.id)
    conn.commit()
    return {"enabled": False}


@router.post("/mfa/step-up")
def mfa_step_up(body: MfaCodeRequest, request: Request, user: CurrentUser = Depends(get_current_user),
                conn: psycopg.Connection = Depends(get_db)):
    """Re-confirm the second factor before a high-risk action."""
    if not user.mfa_enabled:
        return JSONResponse(status_code=400, content={"detail": "Two-step verification is not on"})
    method = _check_second_factor(conn, user.id, body.code)
    if method is None:
        audit.record(conn, user, "MFA_STEP_UP_FAILED", "user", user.id)
        conn.commit()
        security_events.record("MFA_FAILED", organization_id=user.organization_id, user_id=user.id,
                               username=user.username, method="POST", path="/auth/mfa/step-up",
                               status_code=400, ip=user.ip, request_id=audit.current_request_id())
        return JSONResponse(status_code=400, content={"detail": "Incorrect code"})
    conn.execute("UPDATE sessions SET mfa_verified_at = CURRENT_TIMESTAMP WHERE id = %s", (user.session_id,))
    audit.record(conn, user, "MFA_STEP_UP", "user", user.id, None, {"method": method})
    conn.commit()
    return {"verified": True, "valid_minutes": settings.step_up_minutes}


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
        SELECT t.id, t.user_id, t.expires_at, t.used_at, t.revoked_at, t.purpose,
               users.username, users.organization_id, users.is_active
        FROM password_reset_tokens t JOIN users ON users.id = t.user_id
        WHERE t.token_hash = %s
        FOR UPDATE OF t
        """,
        (token_hash(body.token),),
    ).fetchone()
    if row is None or row["used_at"] or row["revoked_at"] or row["expires_at"] <= datetime.now() \
            or not row["is_active"]:
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
    audit.record(conn, None, "PASSWORD_RESET_COMPLETED", "user", row["user_id"], None,
                 {"purpose": row["purpose"]}, username=row["username"], ip=client_ip(request))
    if row["purpose"] == "PLATFORM_RECOVERY":
        audit.platform(conn, None, "RECOVERY_ACCESS_USED", row["organization_id"], "user", row["user_id"],
                       {"username": row["username"]})
    conn.commit()
    return {"message": "Password changed. You can now sign in."}
