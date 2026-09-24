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
Plan = Literal["BASIC", "PROFESSIONAL", "ENTERPRISE"]
Status = Literal["TRIAL", "ACTIVE", "SUSPENDED", "CANCELLED"]


class OrganizationUpdate(BaseModel):
    name: Name150
    org_type: OrgType


class OrganizationCreate(BaseModel):
    name: Name150
    org_type: OrgType = "COMMUNITY_PHARMACY"
    plan: Plan = "BASIC"
    status: Status = "TRIAL"
    admin_username: str = Field(pattern=r"^[A-Za-z0-9._-]{3,50}$")
    admin_full_name: Name150
    admin_password: str = Field(min_length=1, max_length=200)


class PlatformUpdate(BaseModel):
    plan: Plan
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


@router.get("/plans")
def list_plans(user: CurrentUser = Depends(get_current_user)):
    return {
        "plans": {key: {"label": p["label"], "limits": p["limits"], "features": sorted(p["features"])}
                  for key, p in plans.PLANS.items()},
        "feature_labels": plans.FEATURE_LABELS,
        "note": "Plans define capabilities only; pricing is managed by the billing provider.",
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
    return [{**row, "limit_overrides": row["limits"] or {}, **plans.effective(row)} for row in rows]


@router.post("/platform/organizations", tags=["Platform"], status_code=201)
def platform_create(body: OrganizationCreate, user: CurrentUser = Depends(require_step_up),
                    conn: psycopg.Connection = Depends(get_db)):
    validate_password_strength(body.admin_password, body.admin_username)
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
    allowed = {"max_users", "max_locations", "extra_features", "disabled_features"}
    unknown = set(body.limits) - allowed
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown limit keys: {sorted(unknown)}")
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
    return {**row, **plans.effective(row)}
