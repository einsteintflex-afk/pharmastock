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
    for payload in (old_value, new_value):
        if isinstance(payload, dict):
            payload.pop("password_hash", None)
            payload.pop("password", None)

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
