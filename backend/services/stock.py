# ============================================================
# STOCK LEDGER AND FEFO
# ============================================================
# Every change to batches.quantity goes through this module, inside the
# caller's transaction, with the batch row locked (SELECT ... FOR UPDATE).
# Each change writes exactly one stock_movements row, so that for every batch:
#
#   batches.quantity = SUM(RECEIVED + RETURNED + ADJUSTMENT + TRANSFER_IN)
#                    - SUM(DISPENSED + DAMAGED + EXPIRED + TRANSFER_OUT)
#
# (reconciliation() verifies this).
#
# FEFO — First Expiry, First Out: dispensing is allocated to the usable
# (ACTIVE, non-expired) batches of a medicine in order of expiry date, earliest
# first, splitting across batches when one is not enough.

from datetime import date

import psycopg
from fastapi import HTTPException

from .. import audit
from ..security import CurrentUser

INBOUND = ("RECEIVED", "RETURNED")
OUTBOUND = ("DISPENSED", "DAMAGED", "EXPIRED")
# Types that can be recorded directly (POST /stock-movements).
MOVEMENT_TYPES = ("RECEIVED", "DISPENSED", "RETURNED", "DAMAGED", "EXPIRED", "ADJUSTMENT")
# Written only by the transfer workflow.
TRANSFER_TYPES = ("TRANSFER_OUT", "TRANSFER_IN")
ALL_MOVEMENT_TYPES = MOVEMENT_TYPES + TRANSFER_TYPES
# Stored quantity adds to the batch (ADJUSTMENT stores a signed change).
POSITIVE_TYPES = ("RECEIVED", "RETURNED", "ADJUSTMENT", "TRANSFER_IN")

# Manual changes recorded as stock adjustments, with their default reason code.
_ADJUSTMENT_REASONS = {"ADJUSTMENT": "PHYSICAL_COUNT", "DAMAGED": "DAMAGE", "EXPIRED": "EXPIRY_WRITE_OFF",
                       "RETURNED": "CUSTOMER_RETURN"}

# Permission needed to record each movement type.
MOVEMENT_PERMISSIONS = {
    "RECEIVED": "batches.write",
    "DISPENSED": "stock.dispense",
    "RETURNED": "stock.adjust",
    "DAMAGED": "stock.adjust",
    "EXPIRED": "stock.adjust",
    "ADJUSTMENT": "stock.adjust",
}



def signed_quantity_sql(alias: str = "sm") -> str:
    """SQL expression: a movement's effect on its batch quantity."""
    positive = ", ".join(f"'{t}'" for t in POSITIVE_TYPES)
    return f"CASE WHEN {alias}.movement_type IN ({positive}) THEN {alias}.quantity ELSE -{alias}.quantity END"


def signed_quantity(movement_type: str, quantity: int) -> int:
    return quantity if movement_type in POSITIVE_TYPES else -quantity


LEDGER_SUM_SQL = f"COALESCE(SUM({signed_quantity_sql('sm')}), 0)"


def _lock_batch(conn: psycopg.Connection, batch_id: int) -> dict:
    batch = conn.execute(
        """
        SELECT batches.id, batches.medicine_id, batches.batch_number, batches.quantity,
               batches.expiry_date, batches.location_id, batches.batch_status,
               medicines.name AS medicine
        FROM batches
        JOIN medicines ON medicines.id = batches.medicine_id
        WHERE batches.id = %s
        FOR UPDATE OF batches
        """,
        (batch_id,),
    ).fetchone()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    return batch


def insert_movement(
    conn: psycopg.Connection,
    batch_id: int,
    movement_type: str,
    quantity: int,
    reason: str | None,
    user: CurrentUser | None,
    dispensation_item_id: int | None = None,
    transfer_id: int | None = None,
) -> dict:
    return conn.execute(
        """
        INSERT INTO stock_movements (batch_id, movement_type, quantity, reason, user_id, dispensation_item_id,
                                     transfer_id)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, batch_id, movement_type, quantity, movement_date, reason
        """,
        (batch_id, movement_type, quantity, reason, user.id if user else None, dispensation_item_id,
         transfer_id),
    ).fetchone()


def fefo_batches(
    conn: psycopg.Connection,
    medicine_id: int,
    location_id: int | None = None,
    *,
    lock: bool = False,
) -> list[dict]:
    """Usable batches for a medicine in FEFO order (earliest expiry first)."""
    return conn.execute(
        f"""
        SELECT batches.id AS batch_id, batches.batch_number, batches.quantity,
               batches.expiry_date, batches.expiry_date - CURRENT_DATE AS days_until_expiry,
               batches.location_id, locations.name AS location
        FROM batches
        JOIN locations ON locations.id = batches.location_id
        WHERE batches.medicine_id = %(medicine_id)s
          AND batches.quantity > 0
          AND batches.expiry_date >= CURRENT_DATE
          AND batches.batch_status = 'ACTIVE'
          AND (%(location_id)s::int IS NULL OR batches.location_id = %(location_id)s::int)
        ORDER BY batches.expiry_date, batches.id
        {'FOR UPDATE OF batches' if lock else ''}
        """,
        {"medicine_id": medicine_id, "location_id": location_id},
    ).fetchall()


def fefo_plan(
    conn: psycopg.Connection,
    medicine_id: int,
    quantity: int,
    location_id: int | None = None,
    *,
    lock: bool = False,
) -> dict:
    """Allocate `quantity` units across usable batches, earliest expiry first."""
    available = fefo_batches(conn, medicine_id, location_id, lock=lock)
    remaining = quantity
    allocations = []

    for batch in available:
        if remaining <= 0:
            break
        take = min(batch["quantity"], remaining)
        allocations.append({**batch, "allocate": take, "remaining_after": batch["quantity"] - take})
        remaining -= take

    return {
        "medicine_id": medicine_id,
        "requested": quantity,
        "allocated": quantity - remaining,
        "shortfall": remaining,
        "available_usable_stock": sum(b["quantity"] for b in available),
        "allocations": allocations,
        "fefo_batch": available[0] if available else None,
    }


def allocate_fefo(
    conn: psycopg.Connection,
    user: CurrentUser,
    medicine_id: int,
    quantity: int,
    location_id: int | None,
    reason: str | None,
    dispensation_item_id: int | None = None,
) -> list[dict]:
    """Take `quantity` units of a medicine from its usable batches, earliest
    expiry first (batches locked). Raises 400 without changing anything when
    usable stock is insufficient. Returns one entry per batch used."""
    if quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero")

    plan = fefo_plan(conn, medicine_id, quantity, location_id, lock=True)
    if plan["shortfall"] > 0:
        name = conn.execute("SELECT name FROM medicines WHERE id = %s", (medicine_id,)).fetchone()
        raise HTTPException(
            status_code=400,
            detail={
                "message": f"Insufficient usable (non-expired) stock of {name['name'] if name else 'this medicine'}",
                "medicine_id": medicine_id,
                "requested": quantity,
                "available_usable_stock": plan["available_usable_stock"],
            },
        )

    movements = []
    for allocation in plan["allocations"]:
        conn.execute(
            "UPDATE batches SET quantity = quantity - %s WHERE id = %s",
            (allocation["allocate"], allocation["batch_id"]),
        )
        movement = insert_movement(
            conn, allocation["batch_id"], "DISPENSED", allocation["allocate"],
            reason or "Dispensed (FEFO)", user, dispensation_item_id,
        )
        movements.append({
            "movement_id": movement["id"],
            "batch_id": allocation["batch_id"],
            "batch_number": allocation["batch_number"],
            "expiry_date": allocation["expiry_date"],
            "quantity": allocation["allocate"],
            "batch_quantity_after": allocation["remaining_after"],
        })
    return movements


def dispense_fefo(
    conn: psycopg.Connection,
    user: CurrentUser,
    medicine_id: int,
    quantity: int,
    location_id: int | None,
    reason: str | None,
) -> dict:
    """Quick single-medicine dispense (POST /dispense) using genuine FEFO."""
    medicine = conn.execute(
        "SELECT id, name, strength, dosage_form FROM medicines WHERE id = %s", (medicine_id,)
    ).fetchone()
    if medicine is None:
        raise HTTPException(status_code=404, detail="Medicine not found")

    movements = allocate_fefo(conn, user, medicine_id, quantity, location_id, reason)

    audit.record(conn, user, "DISPENSE_FEFO", "medicine", medicine_id, None, {
        "quantity": quantity, "reason": reason,
        "batches": [{"batch_id": m["batch_id"], "quantity": m["quantity"]} for m in movements],
    })
    return {
        "medicine_id": medicine_id,
        "medicine": medicine["name"],
        "strength": medicine["strength"],
        "dosage_form": medicine["dosage_form"],
        "quantity_dispensed": quantity,
        "movements": movements,
    }


def record_movement(
    conn: psycopg.Connection,
    user: CurrentUser,
    batch_id: int,
    movement_type: str,
    quantity: int,
    reason: str | None,
    fefo_override_reason: str | None = None,
    reason_code: str | None = None,
    client_name: str | None = None,
) -> dict:
    """Record one movement against one batch.

    For ADJUSTMENT, `quantity` is the physically counted quantity (the
    original API contract); the signed difference is what the ledger stores.
    """
    movement_type = movement_type.upper().strip()
    reason = (reason or "").strip() or None

    if movement_type in TRANSFER_TYPES:
        raise HTTPException(status_code=400,
                            detail="Transfer movements are recorded through the transfer workflow (/transfers)")
    if movement_type not in MOVEMENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail={"message": "Invalid movement type", "allowed_types": list(MOVEMENT_TYPES)},
        )

    permission = MOVEMENT_PERMISSIONS[movement_type]
    if not user.can(permission):
        raise HTTPException(
            status_code=403,
            detail=f"Your role does not allow recording {movement_type} movements ({permission}).",
        )

    if movement_type == "ADJUSTMENT":
        if quantity < 0:
            raise HTTPException(status_code=400, detail="Counted quantity cannot be negative")
    elif quantity <= 0:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero")

    if movement_type in ("ADJUSTMENT", "DAMAGED") and not reason:
        raise HTTPException(status_code=400, detail=f"A reason is required for {movement_type}")

    if movement_type in _ADJUSTMENT_REASONS:
        # Manual stock changes are adjustments: reason code, previous / new
        # quantity, device, request id, approval thresholds.
        from . import adjustments
        if movement_type == "EXPIRED" and _lock_batch(conn, batch_id)["expiry_date"] >= date.today():
            raise HTTPException(
                status_code=400,
                detail="This batch has not expired. Use DAMAGED or ADJUSTMENT for other write-offs.",
            )
        result = adjustments.create(
            conn, user, batch_id=batch_id, reason_code=reason_code or _ADJUSTMENT_REASONS[movement_type],
            counted_quantity=quantity if movement_type == "ADJUSTMENT" else None,
            change=None if movement_type == "ADJUSTMENT" else signed_quantity(movement_type, quantity),
            notes=reason, client_name=client_name,
        )
        movement = conn.execute("SELECT movement_date FROM stock_movements WHERE id = %s",
                                (result["movement_id"],)).fetchone() if result["movement_id"] else None
        return {
            "id": result["movement_id"],
            "batch_id": batch_id,
            "movement_type": movement_type,
            "quantity": result["adjustment_quantity"] if movement_type == "ADJUSTMENT" else quantity,
            "movement_date": str(movement["movement_date"]) if movement else None,
            "reason": reason,
            "new_batch_quantity": result["new_quantity"],
            "adjustment": {"id": result["id"], "number": result["adjustment_number"], "status": result["status"],
                           "reason_code": result["reason_code"]},
        }

    batch = _lock_batch(conn, batch_id)
    current = batch["quantity"]
    expired = batch["expiry_date"] < date.today()

    if movement_type == "DISPENSED":
        if batch["batch_status"] != "ACTIVE":
            raise HTTPException(
                status_code=400,
                detail=f"This batch is {batch['batch_status'].lower()} and cannot be dispensed.",
            )
        if expired:
            raise HTTPException(
                status_code=400,
                detail="This batch has expired and cannot be dispensed. Record it as EXPIRED instead.",
            )

        fefo = fefo_batches(conn, batch["medicine_id"], batch["location_id"])
        first = fefo[0] if fefo else None
        if first and first["batch_id"] != batch_id and first["expiry_date"] < batch["expiry_date"]:
            if not fefo_override_reason or not fefo_override_reason.strip():
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "FEFO: an earlier-expiring batch must be dispensed first",
                        "fefo_batch_id": first["batch_id"],
                        "fefo_batch_number": first["batch_number"],
                        "fefo_expiry_date": str(first["expiry_date"]),
                        "hint": "Dispense from the FEFO batch, or provide fefo_override_reason (requires permission stock.fefo_override).",
                    },
                )
            if not user.can("stock.fefo_override"):
                raise HTTPException(
                    status_code=403,
                    detail="Your role does not allow overriding FEFO (stock.fefo_override).",
                )
            reason = f"{reason or 'Dispensed'} [FEFO override: {fefo_override_reason.strip()}]"

    if movement_type == "EXPIRED" and not expired:
        raise HTTPException(
            status_code=400,
            detail="This batch has not expired. Use DAMAGED or ADJUSTMENT for other write-offs.",
        )

    if movement_type in INBOUND:
        change = quantity
    elif movement_type in OUTBOUND:
        change = -quantity
    else:  # ADJUSTMENT: counted quantity -> signed change
        change = quantity - current
        if change == 0:
            raise HTTPException(
                status_code=400, detail="Counted quantity equals the recorded quantity; nothing to adjust"
            )

    new_quantity = current + change
    if new_quantity < 0:
        raise HTTPException(status_code=400, detail="Insufficient stock")

    conn.execute("UPDATE batches SET quantity = %s WHERE id = %s", (new_quantity, batch_id))
    ledger_quantity = change if movement_type == "ADJUSTMENT" else quantity
    movement = insert_movement(conn, batch_id, movement_type, ledger_quantity, reason, user)

    audit.record(conn, user, f"STOCK_{movement_type}", "batch", batch_id,
                 {"quantity": current},
                 {"quantity": new_quantity, "movement_id": movement["id"], "reason": reason})

    return {
        "id": movement["id"],
        "batch_id": movement["batch_id"],
        "movement_type": movement["movement_type"],
        "quantity": movement["quantity"],
        "movement_date": str(movement["movement_date"]),
        "reason": movement["reason"],
        "new_batch_quantity": new_quantity,
    }


def reconciliation(conn: psycopg.Connection) -> list[dict]:
    """Batches whose quantity does not equal the sum of their movements."""
    return conn.execute(
        f"""
        SELECT batches.id AS batch_id, medicines.name AS medicine, batches.batch_number,
               batches.quantity AS recorded_quantity,
               {LEDGER_SUM_SQL}::int AS ledger_quantity
        FROM batches
        JOIN medicines ON medicines.id = batches.medicine_id
        LEFT JOIN stock_movements sm ON sm.batch_id = batches.id
        GROUP BY batches.id, medicines.name
        HAVING batches.quantity <> {LEDGER_SUM_SQL}
        ORDER BY batches.id
        """
    ).fetchall()


BATCH_STATUSES = ("ACTIVE", "QUARANTINED", "RECALLED")


def set_batch_status(conn: psycopg.Connection, user: CurrentUser, batch_id: int, status: str,
                     reason: str) -> dict:
    """Place a batch on hold (QUARANTINED / RECALLED) or release it. Held
    stock stays on the books but is excluded from FEFO and dispensing."""
    if status not in BATCH_STATUSES:
        raise HTTPException(status_code=400, detail={"message": "Invalid batch status",
                                                     "allowed": list(BATCH_STATUSES)})
    batch = _lock_batch(conn, batch_id)
    if batch["batch_status"] == status:
        raise HTTPException(status_code=400, detail=f"The batch is already {status.lower()}")
    if batch["batch_status"] == "RECALLED" and status == "ACTIVE" and not user.can("settings.manage"):
        raise HTTPException(status_code=403, detail="Only a manager or administrator can release a recalled batch")
    row = conn.execute(
        """
        UPDATE batches SET batch_status = %s, status_reason = %s, status_changed_at = CURRENT_TIMESTAMP
        WHERE id = %s
        RETURNING id AS batch_id, batch_number, batch_status, status_reason, status_changed_at, quantity
        """,
        (status, reason, batch_id),
    ).fetchone()
    audit.record(conn, user, f"BATCH_{status}", "batch", batch_id,
                 {"batch_status": batch["batch_status"]}, {"batch_status": status, "reason": reason})
    return row


def resolve_mismatch(conn: psycopg.Connection, user: CurrentUser, batch_id: int, action: str,
                     reason: str) -> dict:
    """Resolve a batch whose recorded quantity differs from its ledger.

    TRUST_LEDGER  set the batch quantity to the sum of its movements (the
                  recorded quantity was changed outside the ledger).
    TRUST_COUNT   keep the recorded quantity (confirmed by a physical count)
                  and write one ADJUSTMENT movement for the difference, so
                  the ledger explains the stock.
    """
    batch = _lock_batch(conn, batch_id)
    ledger = conn.execute(
        f"SELECT {LEDGER_SUM_SQL}::int AS ledger FROM stock_movements sm WHERE sm.batch_id = %s", (batch_id,)
    ).fetchone()["ledger"]
    difference = batch["quantity"] - ledger
    if difference == 0:
        raise HTTPException(status_code=400, detail="This batch is already reconciled")

    movement = None
    if action == "TRUST_LEDGER":
        if ledger < 0:
            raise HTTPException(status_code=400, detail="The ledger total is negative; record a count instead")
        conn.execute("UPDATE batches SET quantity = %s WHERE id = %s", (ledger, batch_id))
        new_quantity = ledger
    elif action == "TRUST_COUNT":
        movement = insert_movement(conn, batch_id, "ADJUSTMENT", difference,
                                   f"Reconciliation: {reason}", user)
        new_quantity = batch["quantity"]
    else:
        raise HTTPException(status_code=400, detail="action must be TRUST_LEDGER or TRUST_COUNT")

    audit.record(conn, user, "RECONCILE", "batch", batch_id,
                 {"recorded_quantity": batch["quantity"], "ledger_quantity": ledger},
                 {"action": action, "quantity": new_quantity, "reason": reason,
                  "movement_id": movement["id"] if movement else None})
    return {"batch_id": batch_id, "action": action, "previous_recorded_quantity": batch["quantity"],
            "ledger_quantity_before": ledger, "quantity": new_quantity,
            "adjustment_movement_id": movement["id"] if movement else None}
