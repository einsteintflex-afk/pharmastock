# ============================================================
# STOCK ADJUSTMENTS (reason codes, thresholds, approval)
# ============================================================
# An adjustment records WHY stock changed outside receiving, dispensing and
# transfers: previous quantity, change, new quantity, reason code, notes,
# who, when, from which session / device, and the request id. It posts one
# stock movement, so the ledger rule (batch quantity = sum of movements)
# holds:
#
#   DAMAGE / BREAKAGE      -> DAMAGED   (decrease only)
#   EXPIRY_WRITE_OFF       -> EXPIRED   (decrease only, expired batches)
#   CUSTOMER_RETURN        -> RETURNED  (increase only)
#   anything else          -> ADJUSTMENT (signed change)
#
# Approval: when the change is larger than adjustments.approval_quantity_threshold
# units, or worth more than adjustments.approval_value_threshold, the
# adjustment waits (PENDING_APPROVAL) for a user with stock.approve who is
# not the requester. The pending change is applied as a DELTA at approval,
# so sales made in between are not overwritten. Thresholds of 0 = no approval.

import psycopg
from fastapi import HTTPException

from .. import audit
from ..security import CurrentUser, check_location_scope
from . import app_settings

REASON_CODES = {
    "PHYSICAL_COUNT": "Physical count difference",
    "DAMAGE": "Damaged",
    "BREAKAGE": "Breakage",
    "EXPIRY_WRITE_OFF": "Expiry write-off",
    "THEFT_LOSS": "Theft / loss",
    "SUPPLIER_CORRECTION": "Supplier correction",
    "DATA_ENTRY_CORRECTION": "Data entry correction",
    "CUSTOMER_RETURN": "Customer return",
    "TRANSFER_CORRECTION": "Transfer correction",
    "OPENING_BALANCE": "Opening balance",
    "OTHER": "Other",
}

_MOVEMENT_FOR_REASON = {"DAMAGE": "DAMAGED", "BREAKAGE": "DAMAGED", "EXPIRY_WRITE_OFF": "EXPIRED",
                        "CUSTOMER_RETURN": "RETURNED"}

COLUMNS = """
    a.id, a.adjustment_number, a.batch_id, a.medicine_id, a.location_id, a.previous_quantity,
    a.adjustment_quantity, a.new_quantity, a.unit_cost,
    CASE WHEN a.unit_cost IS NULL THEN NULL ELSE round(abs(a.adjustment_quantity) * a.unit_cost, 2) END AS value,
    a.reason_code, a.notes, a.status, a.requested_at, a.decided_at, a.decision_note, a.movement_id,
    a.stock_count_id, a.client_name, a.request_id,
    m.name AS medicine, m.strength, b.batch_number, l.name AS location,
    ru.full_name AS requested_by_name, a.requested_by, du.full_name AS decided_by_name
"""
FROM = """
    FROM stock_adjustments a
    JOIN medicines m ON m.id = a.medicine_id
    JOIN batches b ON b.id = a.batch_id
    JOIN locations l ON l.id = a.location_id
    JOIN users ru ON ru.id = a.requested_by
    LEFT JOIN users du ON du.id = a.decided_by
"""


def movement_type_for(reason_code: str, change: int) -> str:
    movement = _MOVEMENT_FOR_REASON.get(reason_code, "ADJUSTMENT")
    if movement in ("DAMAGED", "EXPIRED") and change >= 0:
        raise HTTPException(status_code=400, detail=f"{REASON_CODES[reason_code]} can only reduce stock")
    if movement == "RETURNED" and change <= 0:
        raise HTTPException(status_code=400, detail="A customer return can only increase stock")
    return movement


def needs_approval(conn: psycopg.Connection, change: int, unit_cost) -> bool:
    quantity_limit = int(app_settings.get(conn, "adjustments.approval_quantity_threshold") or 0)
    value_limit = float(app_settings.get(conn, "adjustments.approval_value_threshold") or 0)
    if quantity_limit and abs(change) > quantity_limit:
        return True
    # Without a recorded cost the value cannot be judged; the quantity rule still applies.
    if value_limit and unit_cost is not None and abs(change) * float(unit_cost) > value_limit:
        return True
    return False


def get(conn: psycopg.Connection, adjustment_id: int) -> dict:
    row = conn.execute(f"SELECT {COLUMNS} {FROM} WHERE a.id = %s", (adjustment_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Adjustment not found")
    return row


def _lock_batch(conn, batch_id: int) -> dict:
    batch = conn.execute(
        """
        SELECT b.id, b.medicine_id, b.location_id, b.quantity, b.unit_cost, b.expiry_date, b.batch_number
        FROM batches b WHERE b.id = %s FOR UPDATE
        """,
        (batch_id,),
    ).fetchone()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


def create(conn: psycopg.Connection, user: CurrentUser, *, batch_id: int, reason_code: str,
           change: int | None = None, counted_quantity: int | None = None, notes: str | None = None,
           client_name: str | None = None, request_id: str | None = None, stock_count_id: int | None = None,
           approved: bool = False) -> dict:
    """Request an adjustment; it is posted at once unless it needs approval.
    `approved`: the caller already holds an approval (stock count posting)."""
    if reason_code not in REASON_CODES:
        raise HTTPException(status_code=400, detail={"message": "Unknown reason code",
                                                     "reason_codes": list(REASON_CODES)})
    if (change is None) == (counted_quantity is None):
        raise HTTPException(status_code=400, detail="Give either the change or the counted quantity")
    notes = (notes or "").strip() or None
    if reason_code in ("OTHER", "THEFT_LOSS") and not notes:
        raise HTTPException(status_code=400, detail="Notes are required for this reason")

    batch = _lock_batch(conn, batch_id)
    check_location_scope(user, batch["location_id"])
    if counted_quantity is not None:
        if counted_quantity < 0:
            raise HTTPException(status_code=400, detail="Counted quantity cannot be negative")
        change = counted_quantity - batch["quantity"]
    if change == 0:
        raise HTTPException(status_code=400, detail="Counted quantity equals the recorded quantity; nothing to adjust")
    movement_type_for(reason_code, change)
    if reason_code == "EXPIRY_WRITE_OFF":
        from datetime import date
        if batch["expiry_date"] >= date.today():
            raise HTTPException(status_code=400, detail="This batch has not expired")
    if batch["quantity"] + change < 0:
        raise HTTPException(status_code=400, detail="Insufficient stock")

    pending = not approved and needs_approval(conn, change, batch["unit_cost"])
    number = conn.execute("SELECT nextval(pg_get_serial_sequence('stock_adjustments', 'id')) AS id").fetchone()["id"]
    conn.execute(
        """
        INSERT INTO stock_adjustments (id, adjustment_number, batch_id, medicine_id, location_id, previous_quantity,
            adjustment_quantity, unit_cost, reason_code, notes, status, requested_by, session_id, client_name,
            request_id, stock_count_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (number, f"ADJ-{number:06d}", batch_id, batch["medicine_id"], batch["location_id"], batch["quantity"],
         change, batch["unit_cost"], reason_code, notes, "PENDING_APPROVAL" if pending else "POSTED", user.id,
         user.session_id, client_name, request_id or audit.current_request_id(), stock_count_id),
    )
    if pending:
        audit.record(conn, user, "ADJUSTMENT_REQUESTED", "stock_adjustment", number, None,
                     {"batch_id": batch_id, "change": change, "reason_code": reason_code})
        from . import notifications
        notifications.event(
            conn, category="INVENTORY", severity="WARNING",
            title=f"Stock adjustment awaiting approval: {batch['batch_number']}",
            message=f"{user.full_name} requested a change of {change:+d} units ({REASON_CODES[reason_code]}).",
            entity_type="stock_adjustment", entity_id=number, key=f"adjustment:{number}",
        )
    else:
        _post(conn, user, number)
    return get(conn, number)


def _post(conn, user: CurrentUser, adjustment_id: int) -> None:
    from .stock import insert_movement

    row = conn.execute("SELECT * FROM stock_adjustments WHERE id = %s FOR UPDATE", (adjustment_id,)).fetchone()
    batch = _lock_batch(conn, row["batch_id"])
    change = row["adjustment_quantity"]
    new_quantity = batch["quantity"] + change
    if new_quantity < 0:
        raise HTTPException(status_code=400, detail="Insufficient stock: the batch has changed since the request")
    movement_type = movement_type_for(row["reason_code"], change)
    conn.execute("UPDATE batches SET quantity = %s WHERE id = %s", (new_quantity, row["batch_id"]))
    reason = f"{REASON_CODES[row['reason_code']]} ({row['adjustment_number']})" + (
        f": {row['notes']}" if row["notes"] else "")
    movement = insert_movement(conn, row["batch_id"], movement_type,
                               change if movement_type == "ADJUSTMENT" else abs(change), reason, user)
    conn.execute(
        "UPDATE stock_adjustments SET status = 'POSTED', new_quantity = %s, movement_id = %s WHERE id = %s",
        (new_quantity, movement["id"], adjustment_id),
    )
    audit.record(conn, user, f"STOCK_{movement_type}", "batch", row["batch_id"], {"quantity": batch["quantity"]},
                 {"quantity": new_quantity, "movement_id": movement["id"], "adjustment": row["adjustment_number"],
                  "reason_code": row["reason_code"], "notes": row["notes"]})


def decide(conn: psycopg.Connection, user: CurrentUser, adjustment_id: int, approve: bool, note: str | None) -> dict:
    row = conn.execute("SELECT * FROM stock_adjustments WHERE id = %s FOR UPDATE", (adjustment_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Adjustment not found")
    if row["status"] != "PENDING_APPROVAL":
        raise HTTPException(status_code=409, detail=f"Adjustment is already {row['status'].lower().replace('_', ' ')}")
    if row["requested_by"] == user.id:
        raise HTTPException(status_code=403, detail="An adjustment must be approved by someone other than the requester")
    if not approve and not (note or "").strip():
        raise HTTPException(status_code=400, detail="Give a reason for rejecting")
    conn.execute(
        "UPDATE stock_adjustments SET decided_by = %s, decided_at = CURRENT_TIMESTAMP, decision_note = %s, "
        "status = CASE WHEN %s THEN status ELSE 'REJECTED' END WHERE id = %s",
        (user.id, (note or "").strip() or None, approve, adjustment_id),
    )
    if approve:
        _post(conn, user, adjustment_id)
    audit.record(conn, user, "ADJUSTMENT_APPROVED" if approve else "ADJUSTMENT_REJECTED", "stock_adjustment",
                 adjustment_id, None, {"note": note})
    return get(conn, adjustment_id)


def cancel(conn: psycopg.Connection, user: CurrentUser, adjustment_id: int) -> dict:
    row = conn.execute("SELECT * FROM stock_adjustments WHERE id = %s FOR UPDATE", (adjustment_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Adjustment not found")
    if row["status"] != "PENDING_APPROVAL":
        raise HTTPException(status_code=409, detail="Only a pending adjustment can be cancelled")
    if row["requested_by"] != user.id and not user.can("stock.approve"):
        raise HTTPException(status_code=403, detail="Only the requester or an approver can cancel it")
    conn.execute("UPDATE stock_adjustments SET status = 'CANCELLED', decided_by = %s, decided_at = CURRENT_TIMESTAMP "
                 "WHERE id = %s", (user.id, adjustment_id))
    audit.record(conn, user, "ADJUSTMENT_CANCELLED", "stock_adjustment", adjustment_id)
    return get(conn, adjustment_id)


def search(conn: psycopg.Connection, *, status: str | None = None, reason_code: str | None = None,
           location_id: int | None = None, date_from=None, date_to=None, limit: int = 100, offset: int = 0) -> list:
    return conn.execute(
        f"""
        SELECT {COLUMNS}, COUNT(*) OVER () AS total_count {FROM}
        WHERE (%(status)s::text IS NULL OR a.status = %(status)s::text)
          AND (%(reason)s::text IS NULL OR a.reason_code = %(reason)s::text)
          AND (%(location)s::int IS NULL OR a.location_id = %(location)s::int)
          AND (%(date_from)s::date IS NULL OR a.requested_at >= %(date_from)s::date)
          AND (%(date_to)s::date IS NULL OR a.requested_at < %(date_to)s::date + 1)
        ORDER BY a.id DESC LIMIT %(limit)s OFFSET %(offset)s
        """,
        {"status": status, "reason": reason_code, "location": location_id, "date_from": date_from,
         "date_to": date_to, "limit": limit, "offset": offset},
    ).fetchall()
