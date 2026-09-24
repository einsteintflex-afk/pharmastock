# ============================================================
# MEDICINES
# ============================================================
# GET/POST /medicines and PUT /medicines/{id} keep their original request
# and response shapes. GET /medicines gains optional search/sort parameters
# and GET /medicines/{id} returns the full medicine detail.

from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator

from .. import audit
from ..database import get_db
from ..pagination import Page, page_params, paginate
from ..schemas import Name150, Short50, blank_to_none
from ..security import CurrentUser, require
from ..services import analytics, barcode, inventory, plans, stock

router = APIRouter(tags=["Medicines"])


class Medicine(BaseModel):
    id: int
    name: str
    strength: str | None
    dosage_form: str | None
    reorder_level: int
    selling_price: float | None = None
    generic_name: str | None = None
    brand_name: str | None = None
    route: str | None = None
    manufacturer: str | None = None
    gtin: str | None = None
    is_active: bool = True


# Master-data fields added after the original API. On update, a field that
# is omitted keeps its current value, so older clients cannot wipe them.
OPTIONAL_FIELDS = ("selling_price", "generic_name", "brand_name", "route", "manufacturer", "gtin", "is_active")
COLUMNS = ("id, name, strength, dosage_form, reorder_level, selling_price, generic_name, brand_name, route, "
           "manufacturer, gtin, is_active")


class MedicineCreate(BaseModel):
    name: Name150
    strength: Short50 | None = None
    dosage_form: Short50 | None = None
    reorder_level: int = Field(default=20, ge=0, le=10_000_000)
    selling_price: float | None = Field(default=None, ge=0, le=1_000_000)
    generic_name: Name150 | None = None
    brand_name: Name150 | None = None
    route: Short50 | None = None
    manufacturer: Name150 | None = None
    gtin: str | None = Field(default=None, max_length=20)
    is_active: bool = True

    @field_validator("gtin")
    @classmethod
    def _gtin(cls, value):
        try:
            return barcode.normalize_gtin(value)
        except barcode.BarcodeError as error:
            raise ValueError(str(error)) from error


def _clean(body: MedicineCreate) -> tuple:
    return (body.name, blank_to_none(body.strength), blank_to_none(body.dosage_form), body.reorder_level)


def _optional_values(body: MedicineCreate, old: dict | None) -> tuple:
    values = []
    for field in OPTIONAL_FIELDS:
        if old is not None and field not in body.model_fields_set:
            values.append(old[field])
        else:
            value = getattr(body, field)
            values.append(blank_to_none(value) if isinstance(value, str) else value)
    return tuple(values)


def _gtin_check(conn, gtin: str | None, exclude_id: int | None = None) -> None:
    if gtin and conn.execute(
        "SELECT 1 FROM medicines WHERE gtin = %s AND (%s::int IS NULL OR id <> %s::int)",
        (gtin, exclude_id, exclude_id),
    ).fetchone():
        raise HTTPException(status_code=409, detail="Another medicine already has this GTIN / barcode")


def _duplicate_check(conn, body: MedicineCreate, exclude_id: int | None = None) -> None:
    name, strength, dosage_form, _ = _clean(body)
    row = conn.execute(
        """
        SELECT id FROM medicines
        WHERE lower(btrim(name)) = lower(%s)
          AND lower(coalesce(btrim(strength), '')) = lower(coalesce(%s, ''))
          AND lower(coalesce(btrim(dosage_form), '')) = lower(coalesce(%s, ''))
          AND (%s::int IS NULL OR id <> %s::int)
        """,
        (name, strength, dosage_form, exclude_id, exclude_id),
    ).fetchone()
    if row:
        raise HTTPException(
            status_code=409,
            detail="A medicine with the same name, strength and dosage form already exists",
        )


@router.get("/medicines", response_model=list[Medicine])
def get_medicines(
    response: Response,
    search: str | None = Query(default=None, max_length=100),
    sort: Literal["id", "name", "reorder_level"] = "id",
    active: bool | None = None,
    page: Page = Depends(page_params),
    user: CurrentUser = Depends(require("inventory.read")),
    conn: psycopg.Connection = Depends(get_db),
):
    order = {"id": "id", "name": "lower(name), id", "reorder_level": "reorder_level, lower(name)"}[sort]
    rows = conn.execute(
        f"""
        SELECT {COLUMNS}
        FROM medicines
        WHERE (%(search)s::text IS NULL
               OR name ILIKE %(pattern)s OR strength ILIKE %(pattern)s OR dosage_form ILIKE %(pattern)s
               OR generic_name ILIKE %(pattern)s OR brand_name ILIKE %(pattern)s
               OR gtin = lpad(%(search)s::text, 14, '0'))
          AND (%(active)s::boolean IS NULL OR is_active = %(active)s::boolean)
        ORDER BY {order}
        """,
        {"search": search, "pattern": f"%{(search or '').strip()}%", "active": active},
    ).fetchall()
    return paginate(rows, page, response)


@router.post("/medicines", response_model=Medicine)
def create_medicine(body: MedicineCreate, user: CurrentUser = Depends(require("medicines.write")),
                    conn: psycopg.Connection = Depends(get_db)):
    _duplicate_check(conn, body)
    _gtin_check(conn, body.gtin)
    plans.check_limit(conn, user.organization_id, "max_medicines",
                      conn.execute("SELECT COUNT(*) AS n FROM medicines").fetchone()["n"])
    row = conn.execute(
        f"""
        INSERT INTO medicines (name, strength, dosage_form, reorder_level, {', '.join(OPTIONAL_FIELDS)})
        VALUES (%s, %s, %s, %s, {', '.join(['%s'] * len(OPTIONAL_FIELDS))})
        RETURNING {COLUMNS}
        """,
        (*_clean(body), *_optional_values(body, None)),
    ).fetchone()
    audit.record(conn, user, "CREATE", "medicine", row["id"], None, dict(row))
    conn.commit()
    return row


@router.put("/medicines/{medicine_id}", response_model=Medicine)
def update_medicine(medicine_id: int, body: MedicineCreate,
                    user: CurrentUser = Depends(require("medicines.write")),
                    conn: psycopg.Connection = Depends(get_db)):
    old = conn.execute(f"SELECT {COLUMNS} FROM medicines WHERE id = %s FOR UPDATE", (medicine_id,)).fetchone()
    if old is None:
        raise HTTPException(status_code=404, detail="Medicine not found")

    _duplicate_check(conn, body, exclude_id=medicine_id)
    optional = _optional_values(body, old)
    _gtin_check(conn, optional[OPTIONAL_FIELDS.index("gtin")], exclude_id=medicine_id)

    row = conn.execute(
        f"""
        UPDATE medicines SET name = %s, strength = %s, dosage_form = %s, reorder_level = %s,
               {', '.join(f + ' = %s' for f in OPTIONAL_FIELDS)}
        WHERE id = %s
        RETURNING {COLUMNS}
        """,
        (*_clean(body), *optional, medicine_id),
    ).fetchone()
    before, after = audit.changed_fields(dict(old), dict(row))
    if after:
        audit.record(conn, user, "UPDATE", "medicine", medicine_id, before, after)
    conn.commit()
    return row


@router.get("/medicines/{medicine_id}")
def medicine_detail(medicine_id: int, user: CurrentUser = Depends(require("inventory.read")),
                    conn: psycopg.Connection = Depends(get_db)):
    summary = inventory.medicine_stock(conn, medicine_id=medicine_id)
    if not summary:
        raise HTTPException(status_code=404, detail="Medicine not found")

    usage = inventory.consumption(conn, medicine_id).get(medicine_id, {})
    reorder = next(
        (r for r in analytics.reorder_recommendations(conn) if r["medicine_id"] == medicine_id), None
    )
    risk = [r for r in analytics.expiry_risk(conn) if r["medicine_id"] == medicine_id]

    movements = conn.execute(
        """
        SELECT sm.id, sm.batch_id, batches.batch_number, sm.movement_type, sm.quantity,
               sm.movement_date, sm.reason, users.full_name AS user_name
        FROM stock_movements sm
        JOIN batches ON batches.id = sm.batch_id
        LEFT JOIN users ON users.id = sm.user_id
        WHERE batches.medicine_id = %s
        ORDER BY sm.movement_date DESC, sm.id DESC
        LIMIT 50
        """,
        (medicine_id,),
    ).fetchall()

    purchases = conn.execute(
        """
        SELECT po.id AS purchase_order_id, po.order_number, po.order_date, po.status,
               suppliers.name AS supplier, poi.quantity_ordered, poi.quantity_received, poi.unit_cost
        FROM purchase_order_items poi
        JOIN purchase_orders po ON po.id = poi.purchase_order_id
        JOIN suppliers ON suppliers.id = po.supplier_id
        WHERE poi.medicine_id = %s
        ORDER BY po.order_date DESC, po.id DESC
        """,
        (medicine_id,),
    ).fetchall()

    return {
        "medicine": summary[0],
        "consumption": usage,
        "reorder": reorder,
        "batches": inventory.batches(conn, medicine_id=medicine_id),
        "fefo_order": stock.fefo_batches(conn, medicine_id),
        "expiry_risk": risk,
        "movements": movements,
        "purchase_history": purchases,
    }
