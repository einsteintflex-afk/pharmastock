# ============================================================
# AUDIT TRAIL
# ============================================================
# record() writes in the SAME transaction as the change it describes, so an
# action and its audit entry are committed (or rolled back) together.

import json
from datetime import date, datetime
from decimal import Decimal

import psycopg

from .security import CurrentUser


def _default(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return str(value)


def _json(value) -> str | None:
    if value is None:
        return None
    return json.dumps(value, default=_default)


# Keys never written to an audit entry (company or platform), at any depth.
_SECRET_MARKERS = ("password", "secret", "token", "api_key", "apikey", "authorization", "recovery_code")


def scrub(value):
    """Remove secrets (passwords, tokens, keys) from an audit payload."""
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items()
                if not any(marker in str(k).lower() for marker in _SECRET_MARKERS)}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def record(
    conn: psycopg.Connection,
    user: CurrentUser | None,
    action: str,
    entity_type: str,
    entity_id=None,
    old_value: dict | None = None,
    new_value: dict | None = None,
    *,
    username: str | None = None,
    ip: str | None = None,
) -> None:
    # Never store secrets in the audit log.
    old_value, new_value = scrub(old_value), scrub(new_value)

    conn.execute(
        """
        INSERT INTO audit_log
            (user_id, username, action, entity_type, entity_id, old_value, new_value, ip_address)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user.id if user else None,
            user.username if user else username,
            action,
            entity_type,
            None if entity_id is None else str(entity_id),
            _json(old_value),
            _json(new_value),
            user.ip if user else ip,
        ),
    )


def changed_fields(old: dict, new: dict) -> tuple[dict, dict]:
    """Return only the fields that differ, for compact audit entries."""
    keys = [k for k in new if old.get(k) != new.get(k)]
    return {k: old.get(k) for k in keys}, {k: new.get(k) for k in keys}


def platform(
    conn: psycopg.Connection,
    user: CurrentUser | None,
    action: str,
    target_organization_id: int | None = None,
    entity_type: str | None = None,
    entity_id=None,
    details: dict | None = None,
    *,
    reason: str | None = None,
    result: str = "SUCCESS",
    request_id: str | None = None,
) -> None:
    """Platform (MedCart Tech) audit: actions of platform administrators,
    kept apart from the companies' own audit trails. Append-only."""
    conn.execute(
        """
        INSERT INTO platform_audit (actor_user_id, actor_username, action, target_organization_id,
                                    entity_type, entity_id, details, reason, result, ip_address, request_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (user.id if user else None, user.username if user else "system", action, target_organization_id,
         entity_type, None if entity_id is None else str(entity_id), _json(scrub(details)), reason, result,
         user.ip if user else None, request_id or current_request_id()),
    )


def current_request_id() -> str | None:
    from .observability import request_id_var
    value = request_id_var.get()
    return None if value in (None, "-") else value
