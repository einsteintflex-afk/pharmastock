# ============================================================
# NOTIFICATIONS
# ============================================================
# Two kinds of notification:
#
# * Condition notifications (expiry, low stock) are regenerated from live
#   data by refresh(). Each condition has a stable dedupe_key, so refreshing
#   never duplicates; when the condition clears (stock written off, batch
#   received) the notification is resolved automatically.
#
# * Event notifications (purchase order created / cancelled, stock received)
#   are written once by event(), in the same transaction as the event.

import logging

import psycopg

from . import analytics, inventory

logger = logging.getLogger("pharmastock.notifications")

_SEVERITY = {"EXPIRED": "CRITICAL", "CRITICAL": "CRITICAL", "URGENT": "WARNING"}


def _upsert(conn, *, key, category, severity, title, message, entity_type, entity_id) -> None:
    conn.execute(
        """
        INSERT INTO notifications (category, severity, title, message, entity_type, entity_id, dedupe_key)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (organization_id, dedupe_key) DO UPDATE SET
            severity = EXCLUDED.severity,
            title = EXCLUDED.title,
            message = EXCLUDED.message,
            updated_at = CASE
                WHEN notifications.message IS DISTINCT FROM EXCLUDED.message
                  OR notifications.resolved_at IS NOT NULL
                THEN CURRENT_TIMESTAMP ELSE notifications.updated_at END,
            resolved_at = NULL
        """,
        (category, severity, title, message, entity_type, str(entity_id), key),
    )


def refresh(conn: psycopg.Connection) -> dict:
    """Recompute expiry and low-stock notifications from current data."""
    active_keys: list[str] = []

    for batch in inventory.batches(conn, include_empty=False):
        if batch["status"] not in _SEVERITY:
            continue
        days = batch["days_until_expiry"]
        # One key per batch and status: a batch moving from URGENT to CRITICAL
        # raises a new (unread) notification.
        key = f"expiry:{batch['status']}:batch:{batch['batch_id']}"
        when = f"expired {abs(days)} days ago" if days < 0 else f"expires in {days} days"
        _upsert(
            conn,
            key=key,
            category="EXPIRY",
            severity=_SEVERITY[batch["status"]],
            title=f"{batch['status'].title()}: {batch['medicine']} batch {batch['batch_number']}",
            message=(
                f"{batch['quantity']} units of {batch['medicine']} {batch['strength'] or ''} "
                f"(batch {batch['batch_number']}, {batch['location']}) {when} "
                f"on {batch['expiry_date']}."
            ),
            entity_type="batch",
            entity_id=batch["batch_id"],
        )
        active_keys.append(key)

    for item in analytics.reorder_recommendations(conn):
        if item["stock_status"] == inventory.NORMAL and not item["reorder_recommended"]:
            continue
        if item["stock_status"] == inventory.NORMAL:
            status, severity = "REORDER", "INFO"
        elif item["stock_status"] == inventory.OUT_OF_STOCK:
            status, severity = "OUT", "CRITICAL"
        else:
            status, severity = "LOW", "WARNING"
        key = f"stock:{status}:medicine:{item['medicine_id']}"
        suggestion = (
            f" Suggested reorder: {item['recommended_quantity']} units."
            if item["reorder_recommended"] else
            (f" {item['on_order']} units already on order." if item["on_order"] else "")
        )
        _upsert(
            conn,
            key=key,
            category="LOW_STOCK",
            severity=severity,
            title=f"{item['stock_status'].title() if status != 'REORDER' else 'Reorder soon'}: {item['medicine']}",
            message=(
                f"{item['medicine']} {item['strength'] or ''} has {item['usable_stock']} usable units "
                f"(reorder level {item['reorder_level']})." + suggestion
            ),
            entity_type="medicine",
            entity_id=item["medicine_id"],
        )
        active_keys.append(key)

    resolved = conn.execute(
        """
        UPDATE notifications SET resolved_at = CURRENT_TIMESTAMP
        WHERE category IN ('EXPIRY', 'LOW_STOCK')
          AND resolved_at IS NULL
          AND NOT (dedupe_key = ANY(%s))
        """,
        (active_keys,),
    ).rowcount

    return {"active_conditions": len(active_keys), "resolved": resolved}


def event(
    conn: psycopg.Connection,
    *,
    category: str,
    severity: str,
    title: str,
    message: str,
    entity_type: str,
    entity_id,
    key: str,
) -> None:
    """Record a one-off event notification (resolved immediately: it is
    informational and never 'clears')."""
    conn.execute(
        """
        INSERT INTO notifications
            (category, severity, title, message, entity_type, entity_id, dedupe_key, resolved_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (organization_id, dedupe_key) DO NOTHING
        """,
        (category, severity, title, message, entity_type, str(entity_id), key),
    )


def open_item(
    conn: psycopg.Connection,
    *,
    category: str,
    severity: str,
    title: str,
    message: str,
    entity_type: str,
    entity_id,
    key: str,
) -> None:
    """A notification that needs someone to act (for example a transfer
    awaiting approval). It stays open until resolve() is called."""
    _upsert(conn, key=key, category=category, severity=severity, title=title, message=message,
            entity_type=entity_type, entity_id=entity_id)


def resolve(conn: psycopg.Connection, key: str) -> None:
    conn.execute(
        "UPDATE notifications SET resolved_at = CURRENT_TIMESTAMP WHERE dedupe_key = %s AND resolved_at IS NULL",
        (key,),
    )


def list_for_user(conn, user_id: int, *, include_resolved: bool, unread_only: bool, limit: int) -> list[dict]:
    return conn.execute(
        """
        SELECT n.id, n.category, n.severity, n.title, n.message, n.entity_type, n.entity_id,
               n.created_at, n.updated_at, n.resolved_at,
               (r.read_at IS NOT NULL) AS is_read
        FROM notifications n
        LEFT JOIN notification_reads r ON r.notification_id = n.id AND r.user_id = %(user_id)s
        WHERE (%(include_resolved)s OR n.resolved_at IS NULL OR n.category IN ('PURCHASING', 'RECEIVING', 'TRANSFERS'))
          AND (NOT %(unread_only)s OR r.read_at IS NULL)
          AND (n.category NOT IN ('PURCHASING', 'RECEIVING', 'TRANSFERS') OR n.created_at >= CURRENT_TIMESTAMP - INTERVAL '30 days')
        ORDER BY (r.read_at IS NULL) DESC,
                 CASE n.severity WHEN 'CRITICAL' THEN 0 WHEN 'WARNING' THEN 1 ELSE 2 END,
                 n.updated_at DESC
        LIMIT %(limit)s
        """,
        {"user_id": user_id, "include_resolved": include_resolved, "unread_only": unread_only, "limit": limit},
    ).fetchall()


def unread_count(conn, user_id: int) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*) AS unread
        FROM notifications n
        LEFT JOIN notification_reads r ON r.notification_id = n.id AND r.user_id = %s
        WHERE r.read_at IS NULL
          AND (n.resolved_at IS NULL
               OR (n.category IN ('PURCHASING', 'RECEIVING', 'TRANSFERS')
                   AND n.created_at >= CURRENT_TIMESTAMP - INTERVAL '30 days'))
        """,
        (user_id,),
    ).fetchone()
    return row["unread"]
