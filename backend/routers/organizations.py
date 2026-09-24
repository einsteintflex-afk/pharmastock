# ============================================================
# ORGANIZATION (TENANT) AND PLATFORM ADMINISTRATION
# ============================================================
# /organization           the signed-in user's own organization
# /platform/organizations  platform administrators (SaaS operator) only

from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import audit
from ..database import get_db
from ..schemas import Name150
from ..security import CurrentUser, get_current_user, hash_password, require, require_platform_admin, \
    require_step_up, validate_password_strength
from ..services import organizations, plans

router = APIRouter(tags=["Organization"])

OrgType = Literal["COMMUNITY_PHARMACY", "PHARMACY_CHAIN", "HOSPITAL", "WHOLESALE"]
Plan = str  # a code from the plans catalogue (validated against it)
Status = Literal["TRIAL", "ACTIVE", "SUSPENDED", "CANCELLED"]


class OrganizationUpdate(BaseModel):
    name: Name150
    org_type: OrgType


class OrganizationCreate(BaseModel):
    name: Name150
    org_type: OrgType = "COMMUNITY_PHARMACY"
    plan: Plan = Field(default="BASIC", max_length=30)
    status: Status = "TRIAL"
    admin_username: str = Field(pattern=r"^[A-Za-z0-9._-]{3,50}$")
    admin_full_name: Name150
    admin_password: str = Field(min_length=1, max_length=200)


class PlatformUpdate(BaseModel):
    plan: Plan = Field(max_length=30)
    status: Status
    limits: dict = Field(default_factory=dict)
    billing_customer_ref: str | None = Field(default=None, max_length=100)


@router.get("/organization")
def my_organization(user: CurrentUser = Depends(get_current_user), conn: psycopg.Connection = Depends(get_db)):
    return organizations.describe(conn, user.organization_id)


@router.put("/organization")
def update_my_organization(body: OrganizationUpdate, user: CurrentUser = Depends(require("settings.manage")),
                           conn: psycopg.Connection = Depends(get_db)):
    old = plans.organization(conn, user.organization_id)
    conn.execute("UPDATE organizations SET name = %s, org_type = %s WHERE id = %s",
                 (body.name, body.org_type, user.organization_id))
    before, after = audit.changed_fields(
        {"name": old["name"], "org_type": old["org_type"]}, {"name": body.name, "org_type": body.org_type})
    if after:
        audit.record(conn, user, "UPDATE", "organization", user.organization_id, before, after)
    conn.commit()
    return organizations.describe(conn, user.organization_id)


def _check_plan(conn, code: str) -> None:
    plan = plans.catalogue(conn).get(code)
    if plan is None or not plan["is_active"]:
        raise HTTPException(status_code=400, detail=f"Unknown or inactive plan: {code}")


@router.get("/plans")
def list_plans(user: CurrentUser = Depends(get_current_user), conn: psycopg.Connection = Depends(get_db)):
    return {
        "plans": {code: {"label": p["label"], "description": p["description"], "limits": p["limits"],
                         "features": sorted(p["features"]), "price_monthly": p["price_monthly"],
                         "price_annual": p["price_annual"], "currency": p["currency"]}
                  for code, p in plans.catalogue(conn).items() if p["is_active"]},
        "feature_labels": plans.FEATURE_LABELS,
        "limit_labels": plans.LIMIT_LABELS,
        "note": "Prices are set by MedCart Tech; a plan without a price is arranged directly with MedCart Tech.",
    }


# ------------------------------------------------------------
# Platform administration
# ------------------------------------------------------------

@router.get("/platform/organizations", tags=["Platform"])
def platform_list(user: CurrentUser = Depends(require_platform_admin), conn: psycopg.Connection = Depends(get_db)):
    rows = conn.execute(
        """
        SELECT o.*, COUNT(u.id) FILTER (WHERE u.is_active) AS active_users, MAX(u.last_login_at) AS last_activity
        FROM organizations o LEFT JOIN users u ON u.organization_id = o.id
        GROUP BY o.id ORDER BY o.id
        """
    ).fetchall()
    # "limits" becomes the effective limits; the stored overrides stay visible.
    return [{**row, "limit_overrides": row["limits"] or {}, **plans.effective(conn, row)} for row in rows]


@router.post("/platform/organizations", tags=["Platform"], status_code=201)
def platform_create(body: OrganizationCreate, user: CurrentUser = Depends(require_step_up),
                    conn: psycopg.Connection = Depends(get_db)):
    validate_password_strength(body.admin_password, body.admin_username)
    _check_plan(conn, body.plan)
    result = organizations.create(
        conn, name=body.name, org_type=body.org_type, plan=body.plan, status=body.status,
        admin_username=body.admin_username, admin_full_name=body.admin_full_name,
        admin_password_hash=hash_password(body.admin_password),
        return_to_organization=user.organization_id,
    )
    audit.platform(conn, user, "ORGANIZATION_CREATED", result["organization"]["id"], "organization",
                   result["organization"]["id"],
                   {"name": body.name, "plan": body.plan, "org_type": body.org_type, "status": body.status,
                    "owner": body.admin_username})
    conn.commit()
    return result


@router.put("/platform/organizations/{organization_id}", tags=["Platform"])
def platform_update(organization_id: int, body: PlatformUpdate,
                    user: CurrentUser = Depends(require_step_up),
                    conn: psycopg.Connection = Depends(get_db)):
    old = plans.organization(conn, organization_id)
    if organization_id == user.organization_id and body.status in ("SUSPENDED", "CANCELLED"):
        raise HTTPException(status_code=400, detail="You cannot suspend your own organization")
    _check_plan(conn, body.plan)
    allowed = set(plans.LIMIT_LABELS) | {"extra_features", "disabled_features"}
    unknown = set(body.limits) - allowed
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown limit keys: {sorted(unknown)}")
    for key in ("extra_features", "disabled_features"):
        bad = set(body.limits.get(key, [])) - set(plans.FEATURE_LABELS)
        if bad:
            raise HTTPException(status_code=400, detail=f"Unknown features: {sorted(bad)}")
    for key in plans.LIMIT_LABELS:
        value = body.limits.get(key)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise HTTPException(status_code=400, detail=f"{key} must be a whole number or null")
    import json
    row = conn.execute(
        """
        UPDATE organizations SET plan = %s, status = %s, limits = %s, billing_customer_ref = %s
        WHERE id = %s RETURNING *
        """,
        (body.plan, body.status, json.dumps(body.limits), body.billing_customer_ref, organization_id),
    ).fetchone()
    if body.status in ("SUSPENDED", "CANCELLED"):
        conn.execute(
            """
            UPDATE sessions SET revoked_at = CURRENT_TIMESTAMP
            WHERE revoked_at IS NULL AND user_id IN (SELECT id FROM users WHERE organization_id = %s)
            """,
            (organization_id,),
        )
    audit.platform(conn, user, "ORGANIZATION_UPDATED", organization_id, "organization", organization_id,
                   {"before": {"plan": old["plan"], "status": old["status"], "limits": old["limits"]},
                    "after": {"plan": body.plan, "status": body.status, "limits": body.limits}})
    conn.commit()
    return {**row, **plans.effective(conn, row)}


# ------------------------------------------------------------
# Onboarding (first-run set-up of a new organization)
# ------------------------------------------------------------

@router.get("/onboarding")
def onboarding_status(user: CurrentUser = Depends(get_current_user), conn: psycopg.Connection = Depends(get_db)):
    """Nine set-up steps, each checked against real data (nothing is ticked
    by clicking alone, except the optional review steps)."""
    from ..services import app_settings
    org = plans.organization(conn, user.organization_id)
    values = app_settings.get_all(conn)
    count = lambda sql, *p: conn.execute(sql, p).fetchone()["n"]  # noqa: E731
    steps = [
        ("company", "Company profile", "Name, address and phone for receipts and reports", "#/settings",
         bool(values.get("pharmacy.address")) and bool(values.get("pharmacy.phone")), False),
        ("logo", "Company logo", "Shown on receipts and PDF reports", "#/settings",
         count("SELECT COUNT(*) AS n FROM organization_files WHERE kind = 'LOGO'") > 0, True),
        ("locations", "Locations", "Pharmacy, store, wards or branches", "#/locations",
         count("SELECT COUNT(*) AS n FROM locations WHERE is_active") > 0, False),
        ("users", "Team", "Add your staff with the right roles", "#/users",
         count("SELECT COUNT(*) AS n FROM users WHERE organization_id = %s AND is_active", user.organization_id) > 1,
         True),
        ("suppliers", "Suppliers", "Who you buy from", "#/suppliers",
         count("SELECT COUNT(*) AS n FROM suppliers") > 0, False),
        ("medicines", "Medicines", "Scan or add the products you stock", "#/medicines",
         count("SELECT COUNT(*) AS n FROM medicines") > 0, False),
        ("opening_stock", "Opening stock", "Record the batches on your shelves (or run a stock count)", "#/batches",
         count("SELECT COUNT(*) AS n FROM batches") > 0, False),
        ("receipts", "Receipts and tax", "Footer text, tax rate and label", "#/settings",
         bool(values.get("receipt.footer")), True),
        ("security", "Two-step verification", "Protect the owner and administrator accounts", "#/account",
         count("SELECT COUNT(*) AS n FROM users WHERE organization_id = %s AND is_active AND mfa_enabled "
               "AND role IN ('OWNER', 'ADMINISTRATOR')", user.organization_id) > 0, True),
    ]
    items = [{"key": k, "title": t, "description": d, "link": link, "done": done, "optional": optional}
             for k, t, d, link, done, optional in steps]
    required_done = all(i["done"] for i in items if not i["optional"])
    return {"completed_at": org["onboarding_completed_at"], "steps": items,
            "done": sum(i["done"] for i in items), "total": len(items), "required_done": required_done}


@router.post("/onboarding/complete")
def onboarding_complete(user: CurrentUser = Depends(require("settings.manage")),
                        conn: psycopg.Connection = Depends(get_db)):
    conn.execute("UPDATE organizations SET onboarding_completed_at = CURRENT_TIMESTAMP "
                 "WHERE id = %s AND onboarding_completed_at IS NULL", (user.organization_id,))
    audit.record(conn, user, "ONBOARDING_COMPLETED", "organization", user.organization_id)
    conn.commit()
    return onboarding_status(user, conn)
