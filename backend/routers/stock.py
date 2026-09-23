# ============================================================
# STOCK MOVEMENTS
# ============================================================
# POST /stock-movements keeps its original contract (for ADJUSTMENT the
# quantity is the physically counted quantity). It is now permission-checked
# per movement type, enforces FEFO for DISPENSED, and refuses to dispense
# expired stock.

from datetime import date, timedelta
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..database import get_db
from ..schemas import LongText
from ..security import CurrentUser, get_current_user, require
from ..services import stock

router = APIRouter(tags=["Stock movements"])

MovementType = Literal["RECEIVED", "DISPENSED", "RETURNED", "DAMAGED", "EXPIRED", "ADJUSTMENT"]


class StockMovementCreate(BaseModel):
    batch_id: int
    movement_type: str = Field(min_length=1, max_length=20)
    quantity: int = Field(ge=0, le=100_000_000)
    reason: LongText | None = None
    fefo_override_reason: LongText | None = None


@router.post("/stock-movements")
def create_stock_movement(movement: StockMovementCreate,
                          user: CurrentUser = Depends(require("inventory.read")),
                          conn: psycopg.Connection = Depends(get_db)):
    # The type-specific permission is checked inside stock.record_movement.
    result = stock.record_movement(
        conn, user, movement.batch_id, movement.movement_type, movement.quantity,
        movement.reason, movement.fefo_override_reason,
    )
    conn.commit()
    return result


@router.get("/stock-movements")
def get_stock_movements(
    movement_type: MovementType | None = None,
    medicine_id: int | None = None,
    batch_id: int | None = None,
    location_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int | None = Query(default=None, gt=0, le=10_000),
    user: CurrentUser = Depends(require("inventory.read")),
    conn: psycopg.Connection = Depends(get_db),
):
    rows = conn.execute(
        """
        SELECT
            stock_movements.id,
            stock_movements.batch_id,
            medicines.name AS medicine,
            medicines.strength,
            medicines.dosage_form,
            batches.batch_number,
            stock_movements.movement_type,
            stock_movements.quantity,
            stock_movements.movement_date,
            stock_movements.reason,
            medicines.id AS medicine_id,
            locations.name AS location,
            users.full_name AS user_name
        FROM stock_movements
        JOIN batches ON stock_movements.batch_id = batches.id
        JOIN medicines ON batches.medicine_id = medicines.id
        JOIN locations ON batches.location_id = locations.id
        LEFT JOIN users ON users.id = stock_movements.user_id
        WHERE (%(type)s::text IS NULL OR stock_movements.movement_type = %(type)s::text)
          AND (%(medicine_id)s::int IS NULL OR medicines.id = %(medicine_id)s::int)
          AND (%(batch_id)s::int IS NULL OR batches.id = %(batch_id)s::int)
          AND (%(location_id)s::int IS NULL OR batches.location_id = %(location_id)s::int)
          AND (%(date_from)s::date IS NULL OR stock_movements.movement_date >= %(date_from)s::date)
          AND (%(date_to)s::date IS NULL OR stock_movements.movement_date < %(date_to_next)s::date)
        ORDER BY stock_movements.movement_date DESC, stock_movements.id DESC
        LIMIT %(limit)s
        """,
        {
            "type": movement_type, "medicine_id": medicine_id, "batch_id": batch_id,
            "location_id": location_id, "date_from": date_from, "date_to": date_to,
            "date_to_next": (date_to + timedelta(days=1)) if date_to else None, "limit": limit,
        },
    ).fetchall()
    # movement_date as a string, as in the original API.
    return [{**row, "movement_date": str(row["movement_date"])} for row in rows]


@router.get("/stock-movements/types")
def movement_types(user: CurrentUser = Depends(get_current_user)):
    return [
        {"movement_type": t, "permission": stock.MOVEMENT_PERMISSIONS[t],
         "allowed": user.can(stock.MOVEMENT_PERMISSIONS[t])}
        for t in stock.MOVEMENT_TYPES
    ]
