# ============================================================
# SUPPLIERS
# ============================================================

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel

from .. import audit
from ..database import get_db
from ..pagination import Page, page_params, paginate
from ..schemas import Email, LongText, Name150, Phone, blank_to_none
from ..security import CurrentUser, require

router = APIRouter(tags=["Suppliers"])

SUPPLIER_COLUMNS = "id, name, contact_person, phone, email, address, is_active"


class SupplierCreate(BaseModel):
    name: Name150
    contact_person: Name150 | None = None
    phone: Phone | None = None
    email: Email | None = None
    address: LongText | None = None


class Supplier(BaseModel):
    id: int
    name: str
    contact_person: str | None
    phone: str | None
    email: str | None
    address: str | None
    is_active: bool


def _values(body: SupplierCreate) -> tuple:
    return (body.name, blank_to_none(body.contact_person), blank_to_none(body.phone),
            blank_to_none(body.email), blank_to_none(body.address))


def _name_taken(conn, name: str, exclude_id: int | None = None) -> None:
    row = conn.execute(
        "SELECT id FROM suppliers WHERE lower(btrim(name)) = lower(%s) AND (%s::int IS NULL OR id <> %s::int)",
        (name, exclude_id, exclude_id),
    ).fetchone()
    if row:
        raise HTTPException(status_code=409, detail="A supplier with this name already exists")


def _get(conn, supplier_id: int, lock: bool = False) -> dict:
    row = conn.execute(
        f"SELECT {SUPPLIER_COLUMNS} FROM suppliers WHERE id = %s {'FOR UPDATE' if lock else ''}",
        (supplier_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    return row


@router.post("/suppliers", response_model=Supplier)
def create_supplier(supplier: SupplierCreate, user: CurrentUser = Depends(require("suppliers.write")),
                    conn: psycopg.Connection = Depends(get_db)):
    _name_taken(conn, supplier.name)
    row = conn.execute(
        f"""
        INSERT INTO suppliers (name, contact_person, phone, email, address)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING {SUPPLIER_COLUMNS}
        """,
        _values(supplier),
    ).fetchone()
    audit.record(conn, user, "CREATE", "supplier", row["id"], None, dict(row))
    conn.commit()
    return row


@router.get("/suppliers", response_model=list[Supplier])
def get_suppliers(response: Response,
                  search: str | None = Query(default=None, max_length=100),
                  active: bool | None = None,
                  page: Page = Depends(page_params),
                  user: CurrentUser = Depends(require("inventory.read")),
                  conn: psycopg.Connection = Depends(get_db)):
    rows = conn.execute(
        f"""
        SELECT {SUPPLIER_COLUMNS} FROM suppliers
        WHERE (%(search)s::text IS NULL OR name ILIKE %(pattern)s OR contact_person ILIKE %(pattern)s
               OR phone ILIKE %(pattern)s OR email ILIKE %(pattern)s)
          AND (%(active)s::boolean IS NULL OR is_active = %(active)s::boolean)
        ORDER BY name
        """,
        {"search": search, "pattern": f"%{(search or '').strip()}%", "active": active},
    ).fetchall()
    return paginate(rows, page, response)


@router.get("/suppliers/{supplier_id}")
def supplier_detail(supplier_id: int, user: CurrentUser = Depends(require("inventory.read")),
                    conn: psycopg.Connection = Depends(get_db)):
    supplier = _get(conn, supplier_id)

    orders = conn.execute(
        """
        SELECT po.id, po.order_number, po.order_date, po.status, po.notes,
               COUNT(poi.id) AS items,
               COALESCE(SUM(poi.quantity_ordered * poi.unit_cost), 0) AS order_value,
               COALESCE(SUM(poi.quantity_received * poi.unit_cost), 0) AS received_value
        FROM purchase_orders po
        LEFT JOIN purchase_order_items poi ON poi.purchase_order_id = po.id
        WHERE po.supplier_id = %s
        GROUP BY po.id
        ORDER BY po.order_date DESC, po.id DESC
        """,
        (supplier_id,),
    ).fetchall()

    products = conn.execute(
        """
        SELECT medicines.id AS medicine_id, medicines.name AS medicine, medicines.strength,
               medicines.dosage_form,
               SUM(poi.quantity_ordered)::int AS units_ordered,
               SUM(poi.quantity_received)::int AS units_received,
               ROUND(SUM(poi.quantity_ordered * poi.unit_cost) / NULLIF(SUM(poi.quantity_ordered), 0), 2)
                   AS average_unit_cost,
               MAX(po.order_date) AS last_ordered
        FROM purchase_order_items poi
        JOIN purchase_orders po ON po.id = poi.purchase_order_id
        JOIN medicines ON medicines.id = poi.medicine_id
        WHERE po.supplier_id = %s AND po.status <> 'CANCELLED'
        GROUP BY medicines.id
        ORDER BY medicines.name
        """,
        (supplier_id,),
    ).fetchall()

    receipts = conn.execute(
        """
        SELECT pr.id AS receipt_id, pr.received_date, pr.quantity_received, pr.received_by,
               po.id AS purchase_order_id, po.order_number, medicines.id AS medicine_id,
               medicines.name AS medicine, medicines.strength, batches.id AS batch_id,
               batches.batch_number, batches.expiry_date, poi.unit_cost,
               ROUND(pr.quantity_received * poi.unit_cost, 2) AS value
        FROM purchase_receipts pr
        JOIN purchase_order_items poi ON poi.id = pr.purchase_order_item_id
        JOIN purchase_orders po ON po.id = poi.purchase_order_id
        JOIN medicines ON medicines.id = poi.medicine_id
        JOIN batches ON batches.id = pr.batch_id
        WHERE po.supplier_id = %s
        ORDER BY pr.received_date DESC, pr.id DESC
        LIMIT 200
        """,
        (supplier_id,),
    ).fetchall()

    # Activity: changes to the supplier record and to its purchase orders.
    activity = conn.execute(
        """
        SELECT audit_log.occurred_at, audit_log.username, audit_log.action, audit_log.entity_type,
               audit_log.entity_id, po.order_number
        FROM audit_log
        LEFT JOIN purchase_orders po
            ON audit_log.entity_type = 'purchase_order' AND audit_log.entity_id = po.id::text
        WHERE (audit_log.entity_type = 'supplier' AND audit_log.entity_id = %(id)s::text)
           OR po.supplier_id = %(id)s
        ORDER BY audit_log.occurred_at DESC, audit_log.id DESC
        LIMIT 50
        """,
        {"id": supplier_id},
    ).fetchall()

    active_orders = sum(1 for o in orders if o["status"] in ("DRAFT", "ORDERED", "PARTIALLY_RECEIVED"))
    return {
        "supplier": supplier,
        "summary": {
            "orders": len(orders),
            "open_orders": active_orders,
            "total_ordered_value": round(sum(float(o["order_value"]) for o in orders if o["status"] != "CANCELLED"), 2),
            "total_received_value": round(sum(float(o["received_value"]) for o in orders), 2),
            "receipts": len(receipts),
            "units_received": sum(r["quantity_received"] for r in receipts),
            "last_receipt_date": receipts[0]["received_date"] if receipts else None,
        },
        "receipts": receipts,
        "activity": activity,
        "purchase_history": orders,
        "products_supplied": products,
    }


@router.put("/suppliers/{supplier_id}", response_model=Supplier)
def update_supplier(supplier_id: int, body: SupplierCreate,
                    user: CurrentUser = Depends(require("suppliers.write")),
                    conn: psycopg.Connection = Depends(get_db)):
    old = _get(conn, supplier_id, lock=True)
    _name_taken(conn, body.name, exclude_id=supplier_id)
    row = conn.execute(
        f"""
        UPDATE suppliers SET name = %s, contact_person = %s, phone = %s, email = %s, address = %s
        WHERE id = %s
        RETURNING {SUPPLIER_COLUMNS}
        """,
        (*_values(body), supplier_id),
    ).fetchone()
    before, after = audit.changed_fields(dict(old), dict(row))
    if after:
        audit.record(conn, user, "UPDATE", "supplier", supplier_id, before, after)
    conn.commit()
    return row


def _set_active(conn, user, supplier_id: int, active: bool) -> dict:
    old = _get(conn, supplier_id, lock=True)
    if old["is_active"] == active:
        return old
    row = conn.execute(
        f"UPDATE suppliers SET is_active = %s WHERE id = %s RETURNING {SUPPLIER_COLUMNS}",
        (active, supplier_id),
    ).fetchone()
    audit.record(conn, user, "ACTIVATE" if active else "DEACTIVATE", "supplier", supplier_id,
                 {"is_active": old["is_active"]}, {"is_active": active})
    conn.commit()
    return row


@router.post("/suppliers/{supplier_id}/activate", response_model=Supplier)
def activate_supplier(supplier_id: int, user: CurrentUser = Depends(require("suppliers.write")),
                      conn: psycopg.Connection = Depends(get_db)):
    return _set_active(conn, user, supplier_id, True)


@router.post("/suppliers/{supplier_id}/deactivate", response_model=Supplier)
def deactivate_supplier(supplier_id: int, user: CurrentUser = Depends(require("suppliers.write")),
                        conn: psycopg.Connection = Depends(get_db)):
    """Inactive suppliers keep their history but cannot receive new orders."""
    return _set_active(conn, user, supplier_id, False)
