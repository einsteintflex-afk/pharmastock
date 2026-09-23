# ============================================================
# DISPENSING COUNTER
# ============================================================

from datetime import date
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ..database import get_db
from ..schemas import LongText, Name150, Phone, Text255, blank_to_none
from ..security import CurrentUser, require
from ..services import dispensing

router = APIRouter(tags=["Dispensing"])


class DispensationItem(BaseModel):
    medicine_id: int
    quantity: int = Field(gt=0, le=100_000)
    unit_price: float | None = Field(default=None, ge=0, le=1_000_000)
    directions: Text255 | None = None


class DispensationCreate(BaseModel):
    dispense_type: Literal["PRESCRIPTION", "OTC"]
    payment_method: Literal["CASH", "MOBILE_MONEY", "CARD", "NHIS", "INSURANCE", "CREDIT", "NO_CHARGE"]
    items: list[DispensationItem] = Field(min_length=1, max_length=50)
    patient_name: Name150 | None = None
    patient_phone: Phone | None = None
    prescriber: Name150 | None = None
    prescription_number: Text255 | None = None
    location_id: int | None = None
    notes: LongText | None = None


class VoidRequest(BaseModel):
    reason: LongText = Field(min_length=3)


@router.post("/dispensations", status_code=201)
def create_dispensation(body: DispensationCreate, user: CurrentUser = Depends(require("stock.dispense")),
                        conn: psycopg.Connection = Depends(get_db)):
    data = body.model_dump()
    for key in ("patient_name", "patient_phone", "prescriber", "prescription_number", "notes"):
        data[key] = blank_to_none(data[key])
    for item in data["items"]:
        item["directions"] = blank_to_none(item["directions"])
    result = dispensing.create(conn, user, data)
    conn.commit()
    return result


@router.get("/dispensations")
def list_dispensations(date_from: date | None = None, date_to: date | None = None,
                       status: Literal["COMPLETED", "VOIDED"] | None = None,
                       search: str | None = Query(default=None, max_length=100),
                       limit: int = Query(default=500, gt=0, le=5000),
                       user: CurrentUser = Depends(require("inventory.read")),
                       conn: psycopg.Connection = Depends(get_db)):
    return dispensing.search(conn, date_from=date_from, date_to=date_to, status=status,
                             search=search, limit=limit)


@router.get("/dispensations/summary")
def dispensing_summary(day: date | None = None, user: CurrentUser = Depends(require("inventory.read")),
                       conn: psycopg.Connection = Depends(get_db)):
    return dispensing.summary(conn, day or date.today())


@router.get("/dispensations/{dispensation_id}")
def get_dispensation(dispensation_id: int, user: CurrentUser = Depends(require("inventory.read")),
                     conn: psycopg.Connection = Depends(get_db)):
    return dispensing.get(conn, dispensation_id)


@router.post("/dispensations/{dispensation_id}/void")
def void_dispensation(dispensation_id: int, body: VoidRequest,
                      user: CurrentUser = Depends(require("dispensing.void")),
                      conn: psycopg.Connection = Depends(get_db)):
    result = dispensing.void(conn, user, dispensation_id, body.reason)
    conn.commit()
    return result
