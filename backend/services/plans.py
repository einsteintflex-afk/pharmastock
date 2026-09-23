# ============================================================
# ORGANIZATION PLANS, LIMITS AND FEATURES (SaaS readiness)
# ============================================================
# Plans define CAPABILITIES, not prices: commercial pricing is a business
# decision and belongs in the billing provider, not in code. Every limit can
# be overridden per organization (organizations.limits JSON), so a pilot or
# negotiated contract never needs a code change.

from fastapi import HTTPException

# None = unlimited.
PLANS: dict[str, dict] = {
    "BASIC": {
        "label": "Basic",
        "limits": {"max_users": 5, "max_locations": 1},
        "features": {"core", "dispensing", "purchasing", "reports", "notifications"},
    },
    "PROFESSIONAL": {
        "label": "Professional",
        "limits": {"max_users": 25, "max_locations": 5},
        "features": {"core", "dispensing", "purchasing", "reports", "notifications",
                     "advanced_analytics", "ai_assistant", "scheduled_reports", "multi_location"},
    },
    "ENTERPRISE": {
        "label": "Enterprise",
        "limits": {"max_users": None, "max_locations": None},
        "features": {"core", "dispensing", "purchasing", "reports", "notifications",
                     "advanced_analytics", "ai_assistant", "scheduled_reports", "multi_location",
                     "hospital", "api_access"},
    },
}

FEATURE_LABELS = {
    "core": "Inventory, expiry, FEFO",
    "dispensing": "Dispensing counter",
    "purchasing": "Purchasing and receiving",
    "reports": "Reports and exports",
    "notifications": "In-app notifications",
    "advanced_analytics": "Forecasting, turnover and trend analytics",
    "ai_assistant": "AI inventory assistant",
    "scheduled_reports": "Scheduled report delivery",
    "multi_location": "Multiple locations and stock transfers",
    "hospital": "Hospital requisitions and approval workflows",
    "api_access": "Integration API access",
}

ORG_TYPES = ("COMMUNITY_PHARMACY", "PHARMACY_CHAIN", "HOSPITAL", "WHOLESALE")
STATUSES = ("TRIAL", "ACTIVE", "SUSPENDED", "CANCELLED")


def effective(org: dict) -> dict:
    """Limits and features for an organization row (plan + overrides)."""
    plan = PLANS[org["plan"]]
    overrides = org.get("limits") or {}
    limits = {**plan["limits"], **{k: v for k, v in overrides.items() if k in plan["limits"]}}
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
    return feature in effective(organization(conn, organization_id))["features"]


def check_limit(conn, organization_id: int, limit: str, current_count: int) -> None:
    """Raise 403 if adding one more would exceed the organization's limit."""
    maximum = effective(organization(conn, organization_id))["limits"].get(limit)
    if maximum is not None and current_count >= maximum:
        raise HTTPException(
            status_code=403,
            detail=f"Your plan allows at most {maximum} ({limit.replace('max_', '').replace('_', ' ')}). "
                   "Contact your PharmaStock administrator to change plan.",
        )
