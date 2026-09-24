# ============================================================
# AUTHENTICATION AND AUTHORIZATION
# ============================================================
# * Passwords: scrypt (memory-hard, Python standard library) with a random
#   16-byte salt per password; verification is constant-time.
# * Sessions: a random 256-bit token is given to the client (httpOnly
#   SameSite=Strict cookie for the browser, or "Authorization: Bearer" for
#   API/mobile clients). Only its SHA-256 hash is stored. Logout revokes it.
# * Authorization: every protected route declares a permission with
#   require("permission.name"); it is checked here, on the server.

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import psycopg
from fastapi import Depends, HTTPException, Request

from .config import settings
from .database import get_db, set_organization
from .permissions import ADMIN_ROLES, permissions_for
from .services import plans

SESSION_COOKIE = "pharmastock_session"

_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1

MIN_PASSWORD_LENGTH = 10


# ------------------------------------------------------------
# Passwords
# ------------------------------------------------------------

def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return "scrypt${}${}${}${}${}".format(
        _SCRYPT_N, _SCRYPT_R, _SCRYPT_P,
        base64.b64encode(salt).decode(), base64.b64encode(digest).decode(),
    )


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=base64.b64decode(salt_b64),
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


# A hash of a random password, used to spend the same time verifying when
# the username does not exist (prevents username enumeration by timing).
_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def verify_password_or_dummy(password: str, stored: str | None) -> bool:
    if stored is None:
        verify_password(password, _DUMMY_HASH)
        return False
    return verify_password(password, stored)


def validate_password_strength(password: str, username: str | None = None) -> None:
    problems = []
    if len(password) < MIN_PASSWORD_LENGTH:
        problems.append(f"at least {MIN_PASSWORD_LENGTH} characters")
    if not any(c.isalpha() for c in password) or not any(c.isdigit() for c in password):
        problems.append("both letters and numbers")
    if username and username.lower() in password.lower():
        problems.append("must not contain the username")
    if problems:
        raise HTTPException(status_code=400, detail="Password must have " + ", ".join(problems))


# ------------------------------------------------------------
# Sessions
# ------------------------------------------------------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def session_hours(privileged: bool) -> int:
    return min(settings.session_hours, settings.platform_session_hours) if privileged else settings.session_hours


def create_session(conn: psycopg.Connection, user_id: int, request: Request,
                   client_name: str | None = None, *, privileged: bool = False,
                   mfa_verified: bool = False) -> tuple[str, datetime]:
    """privileged: platform administrators get shorter sessions.
    mfa_verified: the sign-in included a second factor."""
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(hours=session_hours(privileged))
    conn.execute(
        """
        INSERT INTO sessions (user_id, token_hash, expires_at, ip_address, user_agent, client_name,
                              privileged, mfa_verified_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, CASE WHEN %s THEN CURRENT_TIMESTAMP END)
        """,
        (user_id, _hash_token(token), expires_at, client_ip(request),
         (request.headers.get("user-agent") or "")[:255], client_name, privileged, mfa_verified),
    )
    return token, expires_at


def token_hash(token: str) -> str:
    return _hash_token(token)


def create_reset_token(conn: psycopg.Connection, user_id: int, purpose: str, created_by: int | None,
                       hours: int) -> tuple[str, datetime]:
    """One-time password reset token; earlier unused tokens of the user are voided."""
    conn.execute("UPDATE password_reset_tokens SET used_at = CURRENT_TIMESTAMP "
                 "WHERE user_id = %s AND used_at IS NULL", (user_id,))
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now() + timedelta(hours=hours)
    conn.execute(
        "INSERT INTO password_reset_tokens (user_id, token_hash, expires_at, created_by, purpose) "
        "VALUES (%s, %s, %s, %s, %s)",
        (user_id, _hash_token(token), expires_at, created_by, purpose),
    )
    return token, expires_at


def revoke_session(conn: psycopg.Connection, token: str) -> None:
    conn.execute(
        "UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP WHERE token_hash = %s AND revoked_at IS NULL",
        (_hash_token(token),),
    )


def revoke_all_sessions(conn: psycopg.Connection, user_id: int, except_token: str | None = None) -> None:
    conn.execute(
        """
        UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP
        WHERE user_id = %s AND revoked_at IS NULL AND token_hash <> %s
        """,
        (user_id, _hash_token(except_token) if except_token else ""),
    )


def request_token(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip() or None
    return request.cookies.get(SESSION_COOKIE)


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


# ------------------------------------------------------------
# Current user
# ------------------------------------------------------------

@dataclass
class CurrentUser:
    id: int
    username: str
    full_name: str
    role: str
    must_change_password: bool
    token: str
    ip: str | None
    permissions: set[str] = field(default_factory=set)
    organization_id: int | None = None
    organization_name: str | None = None
    org_type: str | None = None
    plan: str | None = None
    is_platform_admin: bool = False
    location_id: int | None = None
    features: set[str] = field(default_factory=set)
    session_id: int | None = None
    mfa_enabled: bool = False
    # When this session last proved a second factor (sign-in or step-up).
    mfa_verified_at: datetime | None = None
    # The organization requires MFA for this role and it is not set up yet.
    mfa_setup_required: bool = False

    def can(self, permission: str) -> bool:
        return permission in self.permissions


def get_current_user(request: Request, conn: psycopg.Connection = Depends(get_db)) -> CurrentUser:
    token = request_token(request)

    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    row = conn.execute(
        """
        SELECT users.id, users.username, users.full_name, users.role,
               users.must_change_password, users.is_active, users.organization_id,
               users.is_platform_admin, users.location_id,
               organizations.name AS organization_name, organizations.org_type,
               organizations.plan, organizations.limits, organizations.status AS organization_status,
               users.mfa_enabled, sessions.id AS session_id, sessions.expires_at, sessions.mfa_verified_at
        FROM sessions
        JOIN users ON users.id = sessions.user_id
        JOIN organizations ON organizations.id = users.organization_id
        WHERE sessions.token_hash = %s
          AND sessions.revoked_at IS NULL
        """,
        (_hash_token(token),),
    ).fetchone()

    if row is None or row["expires_at"] <= datetime.now() or not row["is_active"]:
        raise HTTPException(status_code=401, detail="Session expired or invalid. Please sign in again.")
    if row["organization_status"] in ("SUSPENDED", "CANCELLED"):
        raise HTTPException(status_code=401, detail="Your organization's PharmaStock account is not active.")

    # Scope every query in this request to the user's organization.
    set_organization(conn, row["organization_id"])

    conn.execute(
        "UPDATE sessions SET last_seen_at = CURRENT_TIMESTAMP WHERE id = %s", (row["session_id"],)
    )

    user = CurrentUser(
        id=row["id"],
        username=row["username"],
        full_name=row["full_name"],
        role=row["role"],
        must_change_password=row["must_change_password"],
        token=token,
        ip=client_ip(request),
        permissions=permissions_for(row["role"]),
        organization_id=row["organization_id"],
        organization_name=row["organization_name"],
        org_type=row["org_type"],
        plan=row["plan"],
        is_platform_admin=row["is_platform_admin"],
        location_id=row["location_id"],
        features=set(plans.effective({"plan": row["plan"], "limits": row["limits"]})["features"]),
        session_id=row["session_id"],
        mfa_enabled=row["mfa_enabled"],
        mfa_verified_at=row["mfa_verified_at"],
    )
    user.mfa_setup_required = mfa_required_for(conn, user) and not user.mfa_enabled
    request.state.user = user
    return user


def mfa_required_for(conn: psycopg.Connection, user: "CurrentUser") -> bool:
    """Platform administrators always; organization administrators when the
    organization turns on security.require_mfa_for_admins."""
    if user.is_platform_admin:
        return False  # enforced on the platform endpoints (require_platform_admin), not on company work
    if user.role not in ADMIN_ROLES:
        return False
    from .services import app_settings
    return bool(app_settings.get(conn, "security.require_mfa_for_admins"))


def require(permission: str):
    """Route dependency: the signed-in user must hold `permission`."""

    def dependency(request: Request, user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
        # A user with a temporary password may only change it.
        if user.must_change_password:
            raise HTTPException(
                status_code=403, detail="You must change your password before continuing."
            )
        if user.mfa_setup_required:
            raise HTTPException(
                status_code=403,
                detail="Your organization requires two-step verification for your role. "
                       "Set it up under My account → Security before continuing.",
            )
        if not user.can(permission):
            raise HTTPException(
                status_code=403, detail=f"Your role does not allow this action ({permission})."
            )
        return user

    return dependency


def require_feature(feature: str, permission: str = "inventory.read"):
    """Route dependency: permission AND a plan feature of the organization."""

    def dependency(user: CurrentUser = Depends(require(permission))) -> CurrentUser:
        if feature not in user.features:
            raise HTTPException(
                status_code=403,
                detail=f"This feature is not included in your organization's plan ({feature}).",
            )
        return user

    return dependency


def require_platform_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    """Platform (MedCart Tech) console: platform administrator, signed in with
    two-step verification in this session."""
    if user.must_change_password or not user.is_platform_admin:
        raise HTTPException(status_code=403, detail="Platform administrator access required.")
    if not user.mfa_enabled:
        raise HTTPException(status_code=403, detail="Platform access requires two-step verification. "
                                                    "Set it up under My account → Security.")
    if user.mfa_verified_at is None:
        raise HTTPException(status_code=403, detail="Sign in again with your authenticator code to use "
                                                    "the platform console.")
    return user


def require_step_up(user: CurrentUser = Depends(require_platform_admin)) -> CurrentUser:
    """High-risk platform actions: a second factor within the last few minutes."""
    fresh = datetime.now() - timedelta(minutes=settings.step_up_minutes)
    if user.mfa_verified_at is None or user.mfa_verified_at < fresh:
        raise HTTPException(status_code=403, detail="step_up_required: confirm with your authenticator code "
                                                    "to continue.")
    return user
