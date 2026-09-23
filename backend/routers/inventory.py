# ============================================================
# INVENTORY, BATCHES, STOCK ALERTS, FEFO AND DISPENSING
# ============================================================
# Original endpoints kept: GET /inventory, POST /batches, GET /stock-alerts.
# Their original fields are unchanged; new fields are additions.

from datetime import date
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import audit
from ..database import get_db
from ..schemas import Code100, LongText, blank_to_none
from ..security import CurrentUser, require
from ..services import expiry, inventory, stock

router = APIRouter(tags=["Inventory"])

ExpiryStatus = Literal["EXPIRED", "CRITICAL", "URGENT", "APPROACHING EXPIRY", "NORMAL"]


class BatchCreate(BaseModel):
    medicine_id: int
    batch_number: Code100
    quantity: int = Field(ge=0, le=100_000_000)
    expiry_date: date
    unit_cost: float | None = Field(default=None, ge=0, le=10_000_000)
    location_id: int | None = None
    supplier_id: int | None = None
    received_date: date | None = None


class BatchUpdate(BaseModel):
    batch_number: Code100
    expiry_date: date
    unit_cost: float | None = Field(default=None, ge=0, le=10_000_000)
    supplier_id: int | None = None
    received_date: date | None = None
    reason: LongText = Field(min_length=3)


class DispenseRequest(BaseModel):
    medicine_id: int
    quantity: int = Field(gt=0, le=10_000_000)
    location_id: int | None = None
    reason: LongText | None = None


def default_location_id(conn) -> int:
    row = conn.execute(
        "SELECT id FROM locations WHERE is_active ORDER BY id LIMIT 1"
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=400, detail="No active stock location exists")
    return row["id"]


def check_location(conn, location_id: int) -> None:
    row = conn.execute("SELECT is_active FROM locations WHERE id = %s", (location_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Location not found")
    if not row["is_active"]:
        raise HTTPException(status_code=400, detail="Location is inactive")


def check_supplier(conn, supplier_id: int) -> None:
    if conn.execute("SELECT 1 FROM suppliers WHERE id = %s", (supplier_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Supplier not found")


# ------------------------------------------------------------
# Inventory
# ------------------------------------------------------------

@router.get("/inventory")
def get_inventory(
    search: str | None = Query(default=None, max_length=100),
    status: ExpiryStatus | None = None,
    medicine_id: int | None = None,
    location_id: int | None = None,
    supplier_id: int | None = None,
    include_empty: bool = True,
    user: CurrentUser = Depends(require("inventory.read")),
    conn: psycopg.Connection = Depends(get_db),
):
    rows = inventory.batches(
        conn, search=search, status=status, medicine_id=medicine_id, location_id=location_id,
        supplier_id=supplier_id, include_empty=include_empty,
    )
    # Original fields first (expiry_date as a string, as before), then additions.
    return [{**row, "expiry_date": str(row["expiry_date"])} for row in rows]


@router.get("/expiry-alerts")
def expiry_alerts(user: CurrentUser = Depends(require("inventory.read")),
                  conn: psycopg.Connection = Depends(get_db)):
    """Batches with stock that are expired or inside a warning threshold."""
    rows = [r for r in inventory.batches(conn, include_empty=False) if r["status"] != expiry.NORMAL]
    summary = {}
    for status_name in expiry.STATUS_ORDER[:-1]:
        group = [r for r in rows if r["status"] == status_name]
        summary[status_name] = {
            "batches": len(group),
            "units": sum(r["quantity"] for r in group),
            "value": round(sum(float(r["stock_value"] or 0) for r in group), 2),
        }
    return {"summary": summary, "batches": rows}


# ------------------------------------------------------------
# Batches
# ------------------------------------------------------------

@router.post("/batches")
def create_batch(batch: BatchCreate, user: CurrentUser = Depends(require("batches.write")),
                 conn: psycopg.Connection = Depends(get_db)):
    """Register a batch directly (opening stock, donation, stock outside a
    purchase order). The initial quantity is recorded as a RECEIVED movement
    so the stock ledger stays reconciled."""
    if conn.execute("SELECT 1 FROM medicines WHERE id = %s", (batch.medicine_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Medicine not found")

    location_id = batch.location_id or default_location_id(conn)
    check_location(conn, location_id)
    if batch.supplier_id is not None:
        check_supplier(conn, batch.supplier_id)

    exists = conn.execute(
        "SELECT id FROM batches WHERE medicine_id = %s AND batch_number = %s AND location_id = %s",
        (batch.medicine_id, batch.batch_number, location_id),
    ).fetchone()
    if exists:
        raise HTTPException(
            status_code=409,
            detail=f"Batch {batch.batch_number} already exists for this medicine at this location "
                   f"(batch id {exists['id']}). Record a RECEIVED movement against it instead.",
        )

    row = conn.execute(
        """
        INSERT INTO batches (medicine_id, batch_number, quantity, expiry_date, location_id,
                             unit_cost, supplier_id, received_date)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, medicine_id, batch_number, quantity, expiry_date, location_id, unit_cost,
                  supplier_id, received_date
        """,
        (batch.medicine_id, batch.batch_number, batch.quantity, batch.expiry_date, location_id,
         batch.unit_cost, batch.supplier_id, batch.received_date or date.today()),
    ).fetchone()

    if batch.quantity > 0:
        stock.insert_movement(conn, row["id"], "RECEIVED", batch.quantity,
                              "Batch registered (stock entered directly)", user)

    audit.record(conn, user, "CREATE", "batch", row["id"], None, dict(row))
    conn.commit()
    return {**row, "expiry_date": str(row["expiry_date"])}


@router.get("/batches/{batch_id}")
def batch_detail(batch_id: int, user: CurrentUser = Depends(require("inventory.read")),
                 conn: psycopg.Connection = Depends(get_db)):
    rows = inventory.batches(conn, batch_id=batch_id)
    if not rows:
        raise HTTPException(status_code=404, detail="Batch not found")

    history = conn.execute(
        """
        SELECT sm.id, sm.movement_type, sm.quantity, sm.movement_date, sm.reason,
               users.full_name AS user_name
        FROM stock_movements sm
        LEFT JOIN users ON users.id = sm.user_id
        WHERE sm.batch_id = %s
        ORDER BY sm.movement_date, sm.id
        """,
        (batch_id,),
    ).fetchall()

    balance = 0
    for movement in history:
        sign = 1 if movement["movement_type"] in ("RECEIVED", "RETURNED", "ADJUSTMENT") else -1
        balance += sign * movement["quantity"]
        movement["balance_after"] = balance

    receipts = conn.execute(
        """
        SELECT pr.id, pr.received_date, pr.quantity_received, pr.received_by,
               po.id AS purchase_order_id, po.order_number, poi.unit_cost
        FROM purchase_receipts pr
        JOIN purchase_order_items poi ON poi.id = pr.purchase_order_item_id
        JOIN purchase_orders po ON po.id = poi.purchase_order_id
        WHERE pr.batch_id = %s
        ORDER BY pr.received_date
        """,
        (batch_id,),
    ).fetchall()

    return {
        "batch": rows[0],
        "history": history,
        "ledger_balance": balance,
        "reconciled": balance == rows[0]["quantity"],
        "receipts": receipts,
    }


@router.put("/batches/{batch_id}")
def update_batch(batch_id: int, body: BatchUpdate, user: CurrentUser = Depends(require("batches.write")),
                 conn: psycopg.Connection = Depends(get_db)):
    """Correct batch details. Quantity is never edited here: it changes only
    through stock movements, so the ledger stays complete."""
    old = conn.execute(
        """
        SELECT id, medicine_id, location_id, batch_number, expiry_date, unit_cost, supplier_id, received_date
        FROM batches WHERE id = %s FOR UPDATE
        """,
        (batch_id,),
    ).fetchone()
    if old is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    if body.supplier_id is not None:
        check_supplier(conn, body.supplier_id)

    clash = conn.execute(
        """
        SELECT id FROM batches
        WHERE medicine_id = %s AND batch_number = %s AND location_id = %s AND id <> %s
        """,
        (old["medicine_id"], body.batch_number, old["location_id"], batch_id),
    ).fetchone()
    if clash:
        raise HTTPException(status_code=409, detail="Another batch with this number exists at this location")

    row = conn.execute(
        """
        UPDATE batches SET batch_number = %s, expiry_date = %s, unit_cost = %s, supplier_id = %s,
               received_date = %s
        WHERE id = %s
        RETURNING id, batch_number, expiry_date, unit_cost, supplier_id, received_date
        """,
        (body.batch_number, body.expiry_date, body.unit_cost, body.supplier_id, body.received_date, batch_id),
    ).fetchone()

    fields = ["batch_number", "expiry_date", "unit_cost", "supplier_id", "received_date"]
    before, after = audit.changed_fields({k: old[k] for k in fields}, {k: row[k] for k in fields})
    if after:
        after["reason"] = body.reason
        audit.record(conn, user, "UPDATE", "batch", batch_id, before, after)
    conn.commit()
    return row


# ------------------------------------------------------------
# Stock levels (low stock)
# ------------------------------------------------------------

@router.get("/stock-alerts")
def get_stock_alerts(location_id: int | None = None,
                     user: CurrentUser = Depends(require("inventory.read")),
                     conn: psycopg.Connection = Depends(get_db)):
    """Per-medicine stock levels.

    current_stock is USABLE stock (expired batches excluded, since they
    cannot be dispensed); expired_stock and total_stock are listed
    separately. status is OUT OF STOCK / LOW STOCK / NORMAL.
    """
    rows = inventory.medicine_stock(conn, location_id=location_id)
    return [
        {
            "medicine_id": row["medicine_id"],
            "medicine": row["medicine"],
            "strength": row["strength"],
            "dosage_form": row["dosage_form"],
            "current_stock": row["usable_stock"],
            "reorder_level": row["reorder_level"],
            "status": row["stock_status"],
            "expired_stock": row["expired_stock"],
            "total_stock": row["total_stock"],
            "batches_in_stock": row["batches_in_stock"],
            "next_expiry": row["next_expiry"],
            "stock_value": row["stock_value"],
        }
        for row in rows
    ]


# ------------------------------------------------------------
# FEFO and dispensing
# ------------------------------------------------------------

@router.get("/fefo/{medicine_id}")
def fefo_preview(medicine_id: int, quantity: int = Query(default=1, gt=0, le=10_000_000),
                 location_id: int | None = None,
                 user: CurrentUser = Depends(require("inventory.read")),
                 conn: psycopg.Connection = Depends(get_db)):
    """Which batches FEFO would use to supply `quantity` units (no changes made)."""
    if conn.execute("SELECT 1 FROM medicines WHERE id = %s", (medicine_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Medicine not found")
    return stock.fefo_plan(conn, medicine_id, quantity, location_id)


@router.post("/dispense")
def dispense(body: DispenseRequest, user: CurrentUser = Depends(require("stock.dispense")),
             conn: psycopg.Connection = Depends(get_db)):
    """Dispense a quantity of a medicine using FEFO across batches."""
    if body.location_id is not None:
        check_location(conn, body.location_id)
    result = stock.dispense_fefo(conn, user, body.medicine_id, body.quantity, body.location_id,
                                 blank_to_none(body.reason))
    conn.commit()
    return result


@router.get("/stock-reconciliation")
def stock_reconciliation(user: CurrentUser = Depends(require("inventory.read")),
                         conn: psycopg.Connection = Depends(get_db)):
    """Batches whose quantity does not match their movement history. Empty
    means the ledger is fully reconciled."""
    mismatches = stock.reconciliation(conn)
    return {"reconciled": not mismatches, "mismatches": mismatches}
