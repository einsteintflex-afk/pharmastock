# ============================================================
# STOCK ADJUSTMENTS AND STOCK COUNTS
# ============================================================

from datetime import date
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, Field

from .. import idempotency
from ..database import get_db
from ..pagination import set_total
from ..schemas import LongText, Name150
from ..security import CurrentUser, require_feature
from ..services import adjustments, stock_counts

router = APIRouter(tags=["Stock control"])

ReasonCode = Literal[tuple(adjustments.REASON_CODES)]


def _need(permission: str):
    return require_feature("stock_count", permission)


class AdjustmentCreate(BaseModel):
    batch_id: int
    reason_code: ReasonCode
    # Either the signed change, or the counted quantity (the change is derived).
    change: int | None = Field(default=None, ge=-100_000_000, le=100_000_000)
    counted_quantity: int | None = Field(default=None, ge=0, le=100_000_000)
    notes: LongText | None = None


class Decision(BaseModel):
    approve: bool
    note: LongText | None = None


@router.get("/adjustments/reason-codes")
def reason_codes(user: CurrentUser = Depends(_need("inventory.read"))):
    return [{"code": code, "label": label} for code, label in adjustments.REASON_CODES.items()]


@router.get("/adjustments")
def list_adjustments(response: Response, status: str | None = None, reason_code: str | None = None,
                     location_id: int | None = None, date_from: date | None = None, date_to: date | None = None,
                     limit: int = Query(100, ge=1, le=1000), offset: int = Query(0, ge=0),
                     user: CurrentUser = Depends(_need("inventory.read")), conn: psycopg.Connection = Depends(get_db)):
    rows = adjustments.search(conn, status=status, reason_code=reason_code, location_id=location_id,
                              date_from=date_from, date_to=date_to, limit=limit, offset=offset)
    set_total(response, rows)
    return [{k: v for k, v in row.items() if k != "total_count"} for row in rows]


@router.post("/adjustments", status_code=201)
def create_adjustment(body: AdjustmentCreate, request: Request, user: CurrentUser = Depends(_need("stock.adjust")),
                      conn: psycopg.Connection = Depends(get_db)):
    replay = idempotency.begin(conn, user, request, body)
    if replay:
        return replay
    result = adjustments.create(
        conn, user, batch_id=body.batch_id, reason_code=body.reason_code, change=body.change,
        counted_quantity=body.counted_quantity, notes=body.notes,
        client_name=(request.headers.get("x-client-name") or "")[:100] or None,
    )
    idempotency.finish(conn, request, result, 201)
    conn.commit()
    return result


@router.get("/adjustments/{adjustment_id}")
def get_adjustment(adjustment_id: int, user: CurrentUser = Depends(_need("inventory.read")),
                   conn: psycopg.Connection = Depends(get_db)):
    return adjustments.get(conn, adjustment_id)


@router.post("/adjustments/{adjustment_id}/decision")
def decide_adjustment(adjustment_id: int, body: Decision, user: CurrentUser = Depends(_need("stock.approve")),
                      conn: psycopg.Connection = Depends(get_db)):
    result = adjustments.decide(conn, user, adjustment_id, body.approve, body.note)
    conn.commit()
    return result


@router.post("/adjustments/{adjustment_id}/cancel")
def cancel_adjustment(adjustment_id: int, user: CurrentUser = Depends(_need("stock.adjust")),
                      conn: psycopg.Connection = Depends(get_db)):
    result = adjustments.cancel(conn, user, adjustment_id)
    conn.commit()
    return result


# ------------------------------------------------------------
# Stock counts
# ------------------------------------------------------------

class CountCreate(BaseModel):
    location_id: int
    name: Name150 | None = None
    notes: LongText | None = None


class CountLine(BaseModel):
    batch_id: int
    counted_quantity: int = Field(ge=0, le=100_000_000)
    mode: Literal["set", "add"] = "set"
    notes: LongText | None = None


class CountCancel(BaseModel):
    reason: LongText = Field(min_length=3)


@router.get("/stock-counts")
def list_counts(status: str | None = None, location_id: int | None = None,
                user: CurrentUser = Depends(_need("inventory.read")), conn: psycopg.Connection = Depends(get_db)):
    return stock_counts.search(conn, status, location_id)


@router.post("/stock-counts", status_code=201)
def create_count(body: CountCreate, user: CurrentUser = Depends(_need("stock.count")),
                 conn: psycopg.Connection = Depends(get_db)):
    result = stock_counts.create(conn, user, body.location_id, body.name, body.notes)
    conn.commit()
    return result


@router.get("/stock-counts/{count_id}")
def get_count(count_id: int, user: CurrentUser = Depends(_need("inventory.read")),
              conn: psycopg.Connection = Depends(get_db)):
    return stock_counts.detail(conn, count_id)


@router.post("/stock-counts/{count_id}/lines")
def count_line(count_id: int, body: CountLine, user: CurrentUser = Depends(_need("stock.count")),
               conn: psycopg.Connection = Depends(get_db)):
    result = stock_counts.record_line(conn, user, count_id, body.batch_id, body.counted_quantity, body.notes,
                                      body.mode)
    conn.commit()
    return result


@router.delete("/stock-counts/{count_id}/lines/{line_id}")
def delete_count_line(count_id: int, line_id: int, user: CurrentUser = Depends(_need("stock.count")),
                      conn: psycopg.Connection = Depends(get_db)):
    result = stock_counts.remove_line(conn, user, count_id, line_id)
    conn.commit()
    return result


@router.post("/stock-counts/{count_id}/submit")
def submit_count(count_id: int, user: CurrentUser = Depends(_need("stock.count")),
                 conn: psycopg.Connection = Depends(get_db)):
    result = stock_counts.submit(conn, user, count_id)
    conn.commit()
    return result


@router.post("/stock-counts/{count_id}/reopen")
def reopen_count(count_id: int, user: CurrentUser = Depends(_need("stock.approve")),
                 conn: psycopg.Connection = Depends(get_db)):
    result = stock_counts.reopen(conn, user, count_id)
    conn.commit()
    return result


@router.post("/stock-counts/{count_id}/post")
def post_count(count_id: int, request: Request, user: CurrentUser = Depends(_need("stock.approve")),
               conn: psycopg.Connection = Depends(get_db)):
    replay = idempotency.begin(conn, user, request)
    if replay:
        return replay
    result = stock_counts.post(conn, user, count_id)
    idempotency.finish(conn, request, result)
    conn.commit()
    return result


@router.post("/stock-counts/{count_id}/cancel")
def cancel_count(count_id: int, body: CountCancel, user: CurrentUser = Depends(_need("stock.count")),
                 conn: psycopg.Connection = Depends(get_db)):
    result = stock_counts.cancel(conn, user, count_id, body.reason)
    conn.commit()
    return result
