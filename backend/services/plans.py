# ============================================================
# ORGANIZATION PLANS, LIMITS AND FEATURES (entitlements)
# ============================================================
# The plan catalogue lives in the `plans` table and is managed by MedCart
# Tech in the platform console: labels, limits, features and (optional)
# prices can change without a code change. Every limit and feature can also
# be overridden per organization (organizations.limits JSON: limit keys,
# extra_features, disabled_features) for pilots or negotiated contracts.
#
# Entitlements are enforced on the API (security.require_feature,
# check_limit); the frontend only hides what the plan does not include.
#
# The catalogue is cached per process for CACHE_SECONDS; an edit through the
# platform console clears this process's cache immediately and other
# processes pick it up within CACHE_SECONDS.

import threading
import time

import psycopg
from fastapi import HTTPException

CACHE_SECONDS = 30

FEATURE_LABELS = {
    "core": "Inventory, expiry, FEFO",
    "dispensing": "Dispensing counter and receipts",
    "purchasing": "Purchasing and receiving",
    "reports": "Reports and exports",
    "notifications": "In-app notifications",
    "barcode": "Barcode scanning and Scan Center",
    "stock_count": "Stock counts and adjustments",
    "email_receipts": "E-mail receipts",
    "advanced_analytics": "Forecasting, turnover and trend analytics",
    "ai_assistant": "AI inventory assistant",
    "scheduled_reports": "Scheduled report delivery",
    "advanced_reports": "Advanced and branded reports",
    "multi_location": "Multiple locations and stock transfers",
    "purchase_approvals": "Purchase order approval workflow",
    "whatsapp": "WhatsApp messaging (official Business Platform)",
    "sms": "SMS messaging",
    "hospital": "Hospital requisitions and approval workflows",
    "api_access": "Integration API access (reserved)",
}

LIMIT_LABELS = {
    "max_users": "Active users",
    "max_locations": "Active locations",
    "max_medicines": "Medicines",
    "max_scheduled_reports": "Scheduled reports",
}

ORG_TYPES = ("COMMUNITY_PHARMACY", "PHARMACY_CHAIN", "HOSPITAL", "WHOLESALE")
STATUSES = ("TRIAL", "ACTIVE", "SUSPENDED", "CANCELLED")

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "plans": {}}


def invalidate() -> None:
    with _lock:
        _cache["at"] = 0.0


def catalogue(conn: psycopg.Connection) -> dict[str, dict]:
    """code -> plan row (limits dict, features set). Cached briefly."""
    with _lock:
        if time.monotonic() - _cache["at"] < CACHE_SECONDS and _cache["plans"]:
            return _cache["plans"]
    rows = conn.execute("SELECT * FROM plans ORDER BY sort_order, code").fetchall()
    plans = {row["code"]: {**row, "features": set(row["features"] or []), "limits": row["limits"] or {}}
             for row in rows}
    with _lock:
        _cache.update(at=time.monotonic(), plans=plans)
    return plans


def effective(conn: psycopg.Connection, org: dict) -> dict:
    """Limits and features for an organization row (plan + overrides)."""
    plan = catalogue(conn).get(org["plan"])
    if plan is None:
        raise HTTPException(status_code=500, detail=f"Plan {org['plan']} is not in the catalogue")
    overrides = org.get("limits") or {}
    limits = {**{k: None for k in LIMIT_LABELS}, **plan["limits"]}
    limits.update({k: v for k, v in overrides.items() if k in LIMIT_LABELS})
    features = set(plan["features"])
    features |= set(overrides.get("extra_features", []))
    features -= set(overrides.get("disabled_features", []))
    return {"plan": org["plan"], "plan_label": plan["label"], "limits": limits, "features": sorted(features)}


def organization(conn, organization_id: int) -> dict:
    row = conn.execute("SELECT * FROM organizations WHERE id = %s", (organization_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    return row


def has_feature(conn, organization_id: int, feature: str) -> bool:
    return feature in effective(conn, organization(conn, organization_id))["features"]


def check_limit(conn, organization_id: int, limit: str, current_count: int) -> None:
    """Raise 403 if adding one more would exceed the organization's limit."""
    maximum = effective(conn, organization(conn, organization_id))["limits"].get(limit)
    if maximum is not None and current_count >= maximum:
        raise HTTPException(
            status_code=403,
            detail=f"Your plan allows at most {maximum} {LIMIT_LABELS.get(limit, limit).lower()}. "
                   "Upgrade the plan under Billing, or contact MedCart Tech.",
        )
