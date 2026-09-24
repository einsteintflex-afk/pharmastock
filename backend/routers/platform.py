# ============================================================
# PLATFORM OWNER (MEDCART TECH) CONSOLE
# ============================================================
# Only platform administrators, in a session verified with two-step
# verification (require_platform_admin); high-risk actions also need a
# recent step-up (require_step_up). Everything is written to the separate,
# append-only platform audit.
#
# There is NO master password and NO impersonation. When an organization
# loses access to its own administrator account, the platform can issue a
# time-limited, single-use password-set link for one chosen administrator of
# that organization (recovery access). It is recorded in the platform audit
# AND in the organization's own audit trail, so the company can see it.

from datetime import datetime, timedelta

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import audit, security_events
from ..config import settings
from ..database import get_db, set_organization
from ..security import CurrentUser, create_reset_token, require_platform_admin, require_step_up

router = APIRouter(prefix="/platform", tags=["Platform"])

RECOVERY_HOURS = 1


@router.get("/audit")
def platform_audit(action: str | None = None, organization_id: int | None = None,
                   limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                   user: CurrentUser = Depends(require_platform_admin), conn: psycopg.Connection = Depends(get_db)):
    where, params = ["true"], []
    if action:
        where.append("a.action = %s")
        params.append(action.upper())
    if organization_id:
        where.append("a.target_organization_id = %s")
        params.append(organization_id)
    clause = " AND ".join(where)
    total = conn.execute(f"SELECT COUNT(*) AS n FROM platform_audit a WHERE {clause}", params).fetchone()["n"]
    rows = conn.execute(
        f"""
        SELECT a.*, o.name AS organization_name
        FROM platform_audit a LEFT JOIN organizations o ON o.id = a.target_organization_id
        WHERE {clause} ORDER BY a.id DESC LIMIT %s OFFSET %s
        """,
        [*params, limit, offset],
    ).fetchall()
    return {"items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/security-events")
def platform_security_events(event_type: str | None = None, organization_id: int | None = None,
                             hours: int = Query(24, ge=1, le=24 * 90),
                             limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0),
                             user: CurrentUser = Depends(require_platform_admin),
                             conn: psycopg.Connection = Depends(get_db)):
    since = datetime.now() - timedelta(hours=hours)
    where, params = ["e.occurred_at >= %s"], [since]
    if event_type:
        where.append("e.event_type = %s")
        params.append(event_type.upper())
    if organization_id:
        where.append("e.organization_id = %s")
        params.append(organization_id)
    clause = " AND ".join(where)
    summary = conn.execute(
        f"SELECT event_type, COUNT(*) AS count FROM security_events e WHERE {clause} GROUP BY event_type "
        "ORDER BY count DESC", params).fetchall()
    top_ips = conn.execute(
        f"""SELECT ip_address, COUNT(*) AS count, COUNT(DISTINCT event_type) AS kinds
            FROM security_events e WHERE {clause} AND ip_address IS NOT NULL
            GROUP BY ip_address ORDER BY count DESC LIMIT 10""", params).fetchall()
    # Signed-in users repeatedly asking for records outside their organization.
    probes = conn.execute(
        f"""SELECT e.user_id, e.username, o.name AS organization_name, COUNT(*) AS count
            FROM security_events e LEFT JOIN organizations o ON o.id = e.organization_id
            WHERE {clause} AND e.event_type IN ('NOT_FOUND_ID', 'FORBIDDEN') AND e.user_id IS NOT NULL
            GROUP BY e.user_id, e.username, o.name HAVING COUNT(*) >= 10 ORDER BY count DESC LIMIT 20""",
        params).fetchall()
    total = conn.execute(f"SELECT COUNT(*) AS n FROM security_events e WHERE {clause}", params).fetchone()["n"]
    rows = conn.execute(
        f"""
        SELECT e.*, o.name AS organization_name
        FROM security_events e LEFT JOIN organizations o ON o.id = e.organization_id
        WHERE {clause} ORDER BY e.id DESC LIMIT %s OFFSET %s
        """,
        [*params, limit, offset],
    ).fetchall()
    return {"since": since, "summary": summary, "top_ips": top_ips, "suspected_probes": probes,
            "items": rows, "total": total, "limit": limit, "offset": offset}


@router.get("/suspicious-adjustments")
def suspicious_adjustments(days: int = Query(30, ge=1, le=365),
                           user: CurrentUser = Depends(require_platform_admin),
                           conn: psycopg.Connection = Depends(get_db)):
    """Across organizations: unusually large stock adjustments, for the
    platform security review. Counts only; no stock detail leaves a tenant."""
    # Stock data stays under each organization's row level security: the
    # counts are gathered one organization at a time.
    rows = []
    organizations = conn.execute("SELECT id, name FROM organizations ORDER BY id").fetchall()
    for org in organizations:
        set_organization(conn, org["id"])
        counts = conn.execute(
            """
            SELECT COUNT(*) AS adjustments,
                   COUNT(*) FILTER (WHERE abs(adjustment_quantity) >= 100) AS large_adjustments,
                   COUNT(*) FILTER (WHERE reason_code = 'THEFT_LOSS') AS theft_loss,
                   COUNT(*) FILTER (WHERE status = 'REJECTED') AS rejected
            FROM stock_adjustments WHERE requested_at >= CURRENT_DATE - %s::int
            """,
            (days,),
        ).fetchone()
        if counts["adjustments"]:
            rows.append({"organization_id": org["id"], "organization_name": org["name"], **counts})
    set_organization(conn, user.organization_id)
    rows.sort(key=lambda r: (-r["large_adjustments"], -r["adjustments"]))
    return rows


# ------------------------------------------------------------
# Recovery access
# ------------------------------------------------------------

class RecoveryRequest(BaseModel):
    user_id: int
    reason: str = Field(min_length=10, max_length=500)


@router.get("/organizations/{organization_id}/administrators")
def organization_administrators(organization_id: int, user: CurrentUser = Depends(require_platform_admin),
                                conn: psycopg.Connection = Depends(get_db)):
    return conn.execute(
        """
        SELECT id, username, full_name, role, email, is_active, last_login_at, mfa_enabled
        FROM users WHERE organization_id = %s AND role IN ('OWNER', 'ADMINISTRATOR') ORDER BY role DESC, username
        """,
        (organization_id,),
    ).fetchall()


@router.post("/organizations/{organization_id}/recovery-access", status_code=201)
def create_recovery_access(organization_id: int, body: RecoveryRequest, request: Request,
                           user: CurrentUser = Depends(require_step_up),
                           conn: psycopg.Connection = Depends(get_db)):
    target = conn.execute(
        "SELECT id, username, role, is_active FROM users WHERE id = %s AND organization_id = %s",
        (body.user_id, organization_id),
    ).fetchone()
    if target is None or target["role"] not in ("OWNER", "ADMINISTRATOR"):
        raise HTTPException(status_code=404, detail="Administrator not found in this organization")
    if not target["is_active"]:
        raise HTTPException(status_code=400, detail="The account is deactivated")

    set_organization(conn, organization_id)
    token, expires_at = create_reset_token(conn, target["id"], "PLATFORM_RECOVERY", user.id, RECOVERY_HOURS)
    # Lockout and MFA are cleared so the administrator can sign in and set MFA up again.
    conn.execute(
        """
        UPDATE users SET failed_login_count = 0, locked_until = NULL, mfa_enabled = false,
               mfa_secret_encrypted = NULL, mfa_recovery_codes = NULL, mfa_last_counter = NULL
        WHERE id = %s
        """,
        (target["id"],),
    )
    # Visible to the company in its own audit trail.
    audit.record(conn, None, "PLATFORM_RECOVERY_ACCESS", "user", target["id"], None,
                 {"issued_by": f"MedCart Tech platform ({user.username})", "reason": body.reason,
                  "expires_at": expires_at}, username="platform")
    set_organization(conn, user.organization_id)
    audit.platform(conn, user, "RECOVERY_ACCESS_ISSUED", organization_id, "user", target["id"],
                   {"username": target["username"], "expires_at": expires_at}, reason=body.reason)
    conn.commit()
    security_events.record("RECOVERY_ACCESS", organization_id=organization_id, user_id=user.id,
                           username=user.username, method="POST", path=request.url.path, status_code=201,
                           ip=user.ip, request_id=audit.current_request_id(),
                           detail=f"recovery link for {target['username']}")
    base = settings.app_base_url or str(request.base_url).rstrip("/")
    return {"link": f"{base}/app/#/reset-password?token={token}", "expires_at": expires_at,
            "username": target["username"], "single_use": True,
            "note": "Give this link to the verified administrator through a trusted channel. "
                    "It works once and expires in 1 hour."}


@router.get("/recovery-access")
def list_recovery_access(user: CurrentUser = Depends(require_platform_admin),
                         conn: psycopg.Connection = Depends(get_db)):
    return conn.execute(
        """
        SELECT t.id, t.created_at, t.expires_at, t.used_at, t.revoked_at, u.username, u.organization_id,
               o.name AS organization_name, c.username AS issued_by
        FROM password_reset_tokens t
        JOIN users u ON u.id = t.user_id JOIN organizations o ON o.id = u.organization_id
        LEFT JOIN users c ON c.id = t.created_by
        WHERE t.purpose = 'PLATFORM_RECOVERY' ORDER BY t.id DESC LIMIT 100
        """
    ).fetchall()


@router.delete("/recovery-access/{token_id}")
def revoke_recovery_access(token_id: int, user: CurrentUser = Depends(require_platform_admin),
                           conn: psycopg.Connection = Depends(get_db)):
    row = conn.execute(
        """
        UPDATE password_reset_tokens t SET revoked_at = CURRENT_TIMESTAMP
        FROM users u WHERE u.id = t.user_id AND t.id = %s AND t.purpose = 'PLATFORM_RECOVERY'
          AND t.used_at IS NULL AND t.revoked_at IS NULL
        RETURNING t.id, u.organization_id, u.username
        """,
        (token_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No active recovery link with this id")
    audit.platform(conn, user, "RECOVERY_ACCESS_REVOKED", row["organization_id"], "password_reset_token",
                   token_id, {"username": row["username"]})
    conn.commit()
    return {"revoked": token_id}


# ------------------------------------------------------------
# Plan catalogue and entitlements
# ------------------------------------------------------------

class PlanBody(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    description: str | None = Field(default=None, max_length=500)
    limits: dict = Field(default_factory=dict)
    features: list[str] = Field(default_factory=list)
    price_monthly: float | None = Field(default=None, ge=0, le=100_000_000)
    price_annual: float | None = Field(default=None, ge=0, le=100_000_000)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    is_active: bool = True
    sort_order: int = Field(default=0, ge=0, le=100)


def _validate_plan(body: PlanBody) -> None:
    from ..services import plans
    unknown = set(body.features) - set(plans.FEATURE_LABELS)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown features: {sorted(unknown)}")
    bad = set(body.limits) - set(plans.LIMIT_LABELS)
    if bad:
        raise HTTPException(status_code=400, detail=f"Unknown limits: {sorted(bad)}")
    for key, value in body.limits.items():
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise HTTPException(status_code=400, detail=f"{key} must be a whole number or null (unlimited)")
    if (body.price_monthly is not None or body.price_annual is not None) and not body.currency:
        raise HTTPException(status_code=400, detail="A price needs a currency")


@router.get("/plans")
def platform_plans(user: CurrentUser = Depends(require_platform_admin), conn: psycopg.Connection = Depends(get_db)):
    from ..services import plans
    rows = conn.execute(
        """
        SELECT p.*, COUNT(o.id) AS organizations
        FROM plans p LEFT JOIN organizations o ON o.plan = p.code
        GROUP BY p.code ORDER BY p.sort_order, p.code
        """
    ).fetchall()
    return {"plans": rows, "feature_labels": plans.FEATURE_LABELS, "limit_labels": plans.LIMIT_LABELS}


@router.put("/plans/{code}")
def platform_save_plan(code: str, body: PlanBody, user: CurrentUser = Depends(require_step_up),
                       conn: psycopg.Connection = Depends(get_db)):
    import json

    from ..services import plans
    if not code.isupper() or not code.replace("_", "").isalnum() or len(code) > 30:
        raise HTTPException(status_code=400, detail="Plan code: capital letters, digits and _ only")
    _validate_plan(body)
    old = conn.execute("SELECT * FROM plans WHERE code = %s", (code,)).fetchone()
    if old and not body.is_active:
        in_use = conn.execute("SELECT COUNT(*) AS n FROM organizations WHERE plan = %s", (code,)).fetchone()["n"]
        if in_use:
            raise HTTPException(status_code=400, detail=f"{in_use} organization(s) are on this plan; move them first")
    row = conn.execute(
        """
        INSERT INTO plans (code, label, description, limits, features, price_monthly, price_annual, currency,
                           is_active, sort_order, updated_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (code) DO UPDATE SET label = EXCLUDED.label, description = EXCLUDED.description,
            limits = EXCLUDED.limits, features = EXCLUDED.features, price_monthly = EXCLUDED.price_monthly,
            price_annual = EXCLUDED.price_annual, currency = EXCLUDED.currency, is_active = EXCLUDED.is_active,
            sort_order = EXCLUDED.sort_order, updated_by = EXCLUDED.updated_by, updated_at = CURRENT_TIMESTAMP
        RETURNING *
        """,
        (code, body.label, body.description, json.dumps(body.limits), sorted(set(body.features)),
         body.price_monthly, body.price_annual, body.currency, body.is_active, body.sort_order, user.id),
    ).fetchone()
    audit.platform(conn, user, "PLAN_SAVED", None, "plan", code,
                   {"before": {k: old[k] for k in ("limits", "features", "price_monthly", "price_annual", "is_active")}
                    if old else None,
                    "after": {"limits": body.limits, "features": sorted(set(body.features)),
                              "price_monthly": body.price_monthly, "price_annual": body.price_annual,
                              "is_active": body.is_active}})
    conn.commit()
    plans.invalidate()
    return row


class PackageBody(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    credits: int = Field(gt=0, le=10_000_000)
    price: float | None = Field(default=None, ge=0, le=100_000_000)
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    is_active: bool = False


@router.get("/messaging-packages")
def platform_packages(user: CurrentUser = Depends(require_platform_admin), conn: psycopg.Connection = Depends(get_db)):
    return conn.execute("SELECT * FROM messaging_packages ORDER BY credits").fetchall()


@router.put("/messaging-packages/{code}")
def platform_save_package(code: str, body: PackageBody, user: CurrentUser = Depends(require_step_up),
                          conn: psycopg.Connection = Depends(get_db)):
    if not code.replace("_", "").isalnum() or len(code) > 30:
        raise HTTPException(status_code=400, detail="Package code: letters, digits and _ only")
    if body.is_active and (body.price is None or not body.currency):
        raise HTTPException(status_code=400, detail="An active package needs a price and a currency")
    row = conn.execute(
        """
        INSERT INTO messaging_packages (code, label, credits, price, currency, is_active)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (code) DO UPDATE SET label = EXCLUDED.label, credits = EXCLUDED.credits, price = EXCLUDED.price,
            currency = EXCLUDED.currency, is_active = EXCLUDED.is_active, updated_at = CURRENT_TIMESTAMP
        RETURNING *
        """,
        (code.upper(), body.label, body.credits, body.price, body.currency, body.is_active),
    ).fetchone()
    audit.platform(conn, user, "MESSAGING_PACKAGE_SAVED", None, "messaging_package", code.upper(), body.model_dump())
    conn.commit()
    return row


# ------------------------------------------------------------
# Payments and credits
# ------------------------------------------------------------

@router.get("/payments")
def platform_payments(status: str | None = None, limit: int = Query(100, ge=1, le=500),
                      user: CurrentUser = Depends(require_platform_admin), conn: psycopg.Connection = Depends(get_db)):
    where, params = "true", []
    if status:
        where, params = "p.status = %s", [status.upper()]
    return conn.execute(
        f"""
        SELECT p.*, o.name AS organization_name FROM payments p JOIN organizations o ON o.id = p.organization_id
        WHERE {where} ORDER BY p.id DESC LIMIT %s
        """,
        [*params, limit],
    ).fetchall()


class ConfirmPayment(BaseModel):
    note: str = Field(min_length=3, max_length=300)


@router.post("/payments/{payment_id}/confirm")
def platform_confirm_payment(payment_id: int, body: ConfirmPayment, user: CurrentUser = Depends(require_step_up),
                             conn: psycopg.Connection = Depends(get_db)):
    """Manual payments only (bank transfer / mobile money received by MedCart
    Tech). Online payments are confirmed by the provider's webhook."""
    from ..services import billing
    payment = conn.execute("SELECT * FROM payments WHERE id = %s", (payment_id,)).fetchone()
    if payment is None:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment["provider"] != "manual":
        raise HTTPException(status_code=400, detail="Online payments are confirmed only by the payment provider")
    result = billing.mark_paid(conn, payment["reference"], confirmed_by=user.id)
    audit.platform(conn, user, "PAYMENT_CONFIRMED_MANUALLY", payment["organization_id"], "payment", payment_id,
                   {"reference": payment["reference"], "amount": payment["amount"], "currency": payment["currency"],
                    "purpose": payment["purpose"]}, reason=body.note)
    conn.commit()
    set_organization(conn, user.organization_id)
    return result


class CreditGrant(BaseModel):
    change: int = Field(ge=-1_000_000, le=1_000_000)
    reason: str = Field(pattern="^(GRANT|CORRECTION|REFUND)$")
    note: str = Field(min_length=3, max_length=300)


@router.post("/organizations/{organization_id}/credits")
def platform_grant_credits(organization_id: int, body: CreditGrant, user: CurrentUser = Depends(require_step_up),
                           conn: psycopg.Connection = Depends(get_db)):
    from ..services import billing
    if body.change == 0:
        raise HTTPException(status_code=400, detail="Change must not be zero")
    billing.add_credits(conn, organization_id, body.change, body.reason, created_by=user.id, note=body.note)
    audit.platform(conn, user, "MESSAGING_CREDITS_ADJUSTED", organization_id, "messaging_credit_ledger", None,
                   {"change": body.change, "reason_code": body.reason}, reason=body.note)
    set_organization(conn, organization_id)
    balance = billing.credit_balance(conn)
    set_organization(conn, user.organization_id)
    conn.commit()
    return {"organization_id": organization_id, "balance": balance}


@router.get("/usage")
def platform_usage(user: CurrentUser = Depends(require_platform_admin), conn: psycopg.Connection = Depends(get_db)):
    """Per organization: plan, limits, usage, messages this month, credits.
    Tenant tables are read one organization at a time (row level security)."""
    from ..services import billing, plans
    result = []
    for org in conn.execute("SELECT * FROM organizations ORDER BY id").fetchall():
        set_organization(conn, org["id"])
        counts = conn.execute(
            """
            SELECT (SELECT COUNT(*) FROM users WHERE organization_id = %s AND is_active) AS active_users,
                   (SELECT COUNT(*) FROM locations WHERE is_active) AS active_locations,
                   (SELECT COUNT(*) FROM medicines) AS medicines,
                   (SELECT COUNT(*) FROM scheduled_reports) AS scheduled_reports,
                   (SELECT COUNT(*) FROM notification_deliveries
                     WHERE created_at >= date_trunc('month', CURRENT_DATE) AND status = 'SENT') AS messages_this_month,
                   (SELECT COUNT(*) FROM dispensations WHERE dispensed_at >= CURRENT_DATE - 30) AS sales_30_days
            """,
            (org["id"],),
        ).fetchone()
        result.append({
            "organization_id": org["id"], "name": org["name"], "status": org["status"],
            "subscription_status": org["subscription_status"], "messaging_mode": org["messaging_mode"],
            **plans.effective(conn, org), "usage": counts, "credit_balance": billing.credit_balance(conn),
        })
    set_organization(conn, user.organization_id)
    return result
