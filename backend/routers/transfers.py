# ============================================================
# TRANSFERS AND REQUISITIONS
# ============================================================
# Moving stock between locations needs the multi_location plan feature.

from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from ..database import get_db
from ..pagination import set_total
from ..schemas import LongText, blank_to_none
from ..security import CurrentUser, require_feature
from ..services import transfers

router = APIRouter(tags=["Transfers"])

Status = Literal["REQUESTED", "APPROVED", "DISPATCHED", "RECEIVED", "REJECTED", "CANCELLED"]


def _need(permission: str):
    return require_feature("multi_location", permission)


class TransferItem(BaseModel):
    medicine_id: int
    quantity: int = Field(gt=0, le=10_000_000)


class TransferCreate(BaseModel):
    request_type: Literal["TRANSFER", "REQUISITION"] = "TRANSFER"
    from_location_id: int
    to_location_id: int
    priority: Literal["ROUTINE", "URGENT"] = "ROUTINE"
    notes: LongText | None = None
    items: list[TransferItem] = Field(min_length=1, max_length=200)


class Approval(BaseModel):
    # transfer_item_id -> approved quantity; omitted items are approved in full.
    quantities: dict[int, int] | None = None
    note: LongText | None = None


class Reason(BaseModel):
    reason: LongText = Field(min_length=3)


@router.get("/transfers")
def list_transfers(response: Response,
                   status: Status | None = None,
                   request_type: Literal["TRANSFER", "REQUISITION"] | None = None,
                   location_id: int | None = None,
                   open_only: bool = False,
                   limit: int = Query(default=200, gt=0, le=1000),
                   offset: int = Query(default=0, ge=0),
                   user: CurrentUser = Depends(_need("inventory.read")),
                   conn: psycopg.Connection = Depends(get_db)):
    rows = conn.execute(
        transfers.LIST_SQL + """
        WHERE (%(status)s::text IS NULL OR t.status = %(status)s::text)
          AND (%(type)s::text IS NULL OR t.request_type = %(type)s::text)
          AND (%(loc)s::int IS NULL OR t.from_location_id = %(loc)s::int OR t.to_location_id = %(loc)s::int)
          AND (NOT %(open)s OR t.status IN ('REQUESTED', 'APPROVED', 'DISPATCHED'))
        ORDER BY CASE t.status WHEN 'REQUESTED' THEN 0 WHEN 'APPROVED' THEN 1 WHEN 'DISPATCHED' THEN 2 ELSE 3 END,
                 (t.priority = 'URGENT') DESC, t.requested_at DESC, t.id DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {"status": status, "type": request_type, "loc": location_id, "open": open_only,
         "limit": limit, "offset": offset},
    ).fetchall()
    return set_total(response, rows)


@router.post("/transfers", status_code=201)
def create_transfer(body: TransferCreate, user: CurrentUser = Depends(_need("transfers.request")),
                    conn: psycopg.Connection = Depends(get_db)):
    transfer = transfers.create(
        conn, user, request_type=body.request_type, from_location_id=body.from_location_id,
        to_location_id=body.to_location_id, items=[i.model_dump() for i in body.items],
        priority=body.priority, notes=blank_to_none(body.notes),
    )
    conn.commit()
    return transfers.detail(conn, transfer["id"])


@router.get("/transfers/{transfer_id}")
def transfer_detail(transfer_id: int, user: CurrentUser = Depends(_need("inventory.read")),
                    conn: psycopg.Connection = Depends(get_db)):
    return transfers.detail(conn, transfer_id)


@router.post("/transfers/{transfer_id}/approve")
def approve_transfer(transfer_id: int, body: Approval, user: CurrentUser = Depends(_need("transfers.approve")),
                     conn: psycopg.Connection = Depends(get_db)):
    transfers.approve(conn, user, transfer_id, body.quantities, blank_to_none(body.note))
    conn.commit()
    return transfers.detail(conn, transfer_id)


@router.post("/transfers/{transfer_id}/reject")
def reject_transfer(transfer_id: int, body: Reason, user: CurrentUser = Depends(_need("transfers.approve")),
                    conn: psycopg.Connection = Depends(get_db)):
    transfers.reject(conn, user, transfer_id, body.reason)
    conn.commit()
    return transfers.detail(conn, transfer_id)


@router.post("/transfers/{transfer_id}/cancel")
def cancel_transfer(transfer_id: int, body: Reason, user: CurrentUser = Depends(_need("transfers.request")),
                    conn: psycopg.Connection = Depends(get_db)):
    transfers.cancel(conn, user, transfer_id, body.reason)
    conn.commit()
    return transfers.detail(conn, transfer_id)


@router.post("/transfers/{transfer_id}/dispatch")
def dispatch_transfer(transfer_id: int, user: CurrentUser = Depends(_need("transfers.dispatch")),
                      conn: psycopg.Connection = Depends(get_db)):
    transfers.dispatch(conn, user, transfer_id)
    conn.commit()
    return transfers.detail(conn, transfer_id)


@router.post("/transfers/{transfer_id}/receive")
def receive_transfer(transfer_id: int, user: CurrentUser = Depends(_need("transfers.receive")),
                     conn: psycopg.Connection = Depends(get_db)):
    transfers.receive(conn, user, transfer_id)
    conn.commit()
    return transfers.detail(conn, transfer_id)
