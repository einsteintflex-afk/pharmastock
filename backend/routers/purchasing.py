# ============================================================
# PURCHASING AND RECEIVING
# ============================================================
# Original endpoints and behaviour kept:
#   POST/GET /purchase-orders, POST/GET /purchase-orders/{id}/items,
#   POST/GET /purchase-receipts
# (including: the first item moves a DRAFT order to ORDERED; receiving into
# an existing batch number adds to that batch; order status is recomputed
# after every receipt).
#
# Added: order detail, notes edit, item edit/remove, cancellation, receiving
# into a chosen location, batch cost/supplier capture, notifications.

from datetime import date

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .. import audit, idempotency
from ..database import get_db
from ..schemas import Code50, Code100, LongText, Name150, blank_to_none
from ..security import CurrentUser, require
from ..services import notifications, stock
from .inventory import check_location, default_location_id

router = APIRouter(tags=["Purchasing"])

OPEN_STATUSES = ("DRAFT", "ORDERED", "PARTIALLY_RECEIVED")


class PurchaseOrderCreate(BaseModel):
    supplier_id: int
    order_number: Code50
    notes: LongText | None = None


class PurchaseOrderUpdate(BaseModel):
    notes: LongText | None = None


class PurchaseOrderItemCreate(BaseModel):
    medicine_id: int
    quantity_ordered: int = Field(gt=0, le=100_000_000)
    unit_cost: float = Field(ge=0, le=10_000_000)


class PurchaseOrderItemUpdate(BaseModel):
    quantity_ordered: int = Field(gt=0, le=100_000_000)
    unit_cost: float = Field(ge=0, le=10_000_000)


class CancelRequest(BaseModel):
    reason: LongText = Field(min_length=3)


class PurchaseReceiptCreate(BaseModel):
    purchase_order_item_id: int
    batch_number: Code100
    quantity_received: int = Field(gt=0, le=100_000_000)
    expiry_date: date
    received_by: Name150 | None = None
    notes: LongText | None = None
    location_id: int | None = None
    barcode_data: str | None = Field(default=None, max_length=200)


def _lock_order(conn, order_id: int) -> dict:
    order = conn.execute(
        """
        SELECT po.*, suppliers.name AS supplier
        FROM purchase_orders po JOIN suppliers ON suppliers.id = po.supplier_id
        WHERE po.id = %s FOR UPDATE OF po
        """,
        (order_id,),
    ).fetchone()
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")
    return order


def _recompute_status(conn, order_id: int) -> str:
    summary = conn.execute(
        """
        SELECT COUNT(*) AS total_items,
               COUNT(*) FILTER (WHERE quantity_received >= quantity_ordered) AS fully_received_items,
               COALESCE(SUM(quantity_received), 0) AS total_received
        FROM purchase_order_items WHERE purchase_order_id = %s
        """,
        (order_id,),
    ).fetchone()

    if summary["total_items"] > 0 and summary["fully_received_items"] == summary["total_items"]:
        status = "RECEIVED"
    elif summary["total_received"] > 0:
        status = "PARTIALLY_RECEIVED"
    else:
        status = "ORDERED"

    conn.execute("UPDATE purchase_orders SET status = %s WHERE id = %s", (status, order_id))
    return status


# ------------------------------------------------------------
# Purchase orders
# ------------------------------------------------------------

@router.get("/purchase-orders/next-number")
def next_order_number(user: CurrentUser = Depends(require("inventory.read")),
                      conn: psycopg.Connection = Depends(get_db)):
    """Suggest the next free order number in the PO-YYYY-NNN pattern."""
    prefix = f"PO-{date.today().year}-"
    rows = conn.execute(
        "SELECT order_number FROM purchase_orders WHERE order_number LIKE %s", (prefix + "%",)
    ).fetchall()
    numbers = [int(r["order_number"][len(prefix):]) for r in rows if r["order_number"][len(prefix):].isdigit()]
    return {"order_number": f"{prefix}{(max(numbers, default=0) + 1):03d}"}


@router.post("/purchase-orders")
def create_purchase_order(order: PurchaseOrderCreate,
                          user: CurrentUser = Depends(require("purchasing.write")),
                          conn: psycopg.Connection = Depends(get_db)):
    supplier = conn.execute(
        "SELECT id, name, is_active FROM suppliers WHERE id = %s", (order.supplier_id,)
    ).fetchone()
    if supplier is None:
        raise HTTPException(status_code=404, detail="Supplier not found")
    if not supplier["is_active"]:
        raise HTTPException(status_code=400, detail="Supplier is inactive")

    if conn.execute("SELECT 1 FROM purchase_orders WHERE order_number = %s", (order.order_number,)).fetchone():
        raise HTTPException(status_code=400, detail="Purchase order number already exists")

    row = conn.execute(
        """
        INSERT INTO purchase_orders (supplier_id, order_number, notes, created_by)
        VALUES (%s, %s, %s, %s)
        RETURNING id, supplier_id, order_number, order_date, status, notes, created_at
        """,
        (order.supplier_id, order.order_number, blank_to_none(order.notes), user.id),
    ).fetchone()

    audit.record(conn, user, "CREATE", "purchase_order", row["id"], None, dict(row))
    notifications.event(
        conn, category="PURCHASING", severity="INFO",
        title=f"Purchase order {row['order_number']} created",
        message=f"{user.full_name} created purchase order {row['order_number']} for {supplier['name']}.",
        entity_type="purchase_order", entity_id=row["id"], key=f"po:created:{row['id']}",
    )
    conn.commit()
    return {**row, "order_date": str(row["order_date"]), "created_at": str(row["created_at"])}


@router.get("/purchase-orders")
def get_purchase_orders(status: str | None = Query(default=None, max_length=30),
                        supplier_id: int | None = None,
                        user: CurrentUser = Depends(require("inventory.read")),
                        conn: psycopg.Connection = Depends(get_db)):
    rows = conn.execute(
        """
        SELECT po.id, po.supplier_id, suppliers.name AS supplier, po.order_number, po.order_date,
               po.status, po.notes, po.created_at,
               COUNT(poi.id) AS items,
               COALESCE(SUM(poi.quantity_ordered * poi.unit_cost), 0) AS order_value,
               COALESCE(SUM(poi.quantity_ordered), 0)::int AS units_ordered,
               COALESCE(SUM(poi.quantity_received), 0)::int AS units_received,
               users.full_name AS created_by_name
        FROM purchase_orders po
        JOIN suppliers ON po.supplier_id = suppliers.id
        LEFT JOIN purchase_order_items poi ON poi.purchase_order_id = po.id
        LEFT JOIN users ON users.id = po.created_by
        WHERE (%(status)s::text IS NULL OR po.status = %(status)s::text)
          AND (%(supplier_id)s::int IS NULL OR po.supplier_id = %(supplier_id)s::int)
        GROUP BY po.id, suppliers.name, users.full_name
        ORDER BY po.id DESC
        """,
        {"status": status, "supplier_id": supplier_id},
    ).fetchall()
    return [{**r, "order_date": str(r["order_date"]), "created_at": str(r["created_at"])} for r in rows]


@router.get("/purchase-orders/{purchase_order_id}")
def purchase_order_detail(purchase_order_id: int, user: CurrentUser = Depends(require("inventory.read")),
                          conn: psycopg.Connection = Depends(get_db)):
    order = conn.execute(
        """
        SELECT po.*, suppliers.name AS supplier, suppliers.is_active AS supplier_active,
               users.full_name AS created_by_name
        FROM purchase_orders po
        JOIN suppliers ON suppliers.id = po.supplier_id
        LEFT JOIN users ON users.id = po.created_by
        WHERE po.id = %s
        """,
        (purchase_order_id,),
    ).fetchone()
    if order is None:
        raise HTTPException(status_code=404, detail="Purchase order not found")

    items = get_purchase_order_items(purchase_order_id, user, conn)
    receipts = conn.execute(
        """
        SELECT pr.id, pr.purchase_order_item_id, medicines.name AS medicine, batches.batch_number,
               batches.expiry_date, locations.name AS location, pr.quantity_received, pr.received_date,
               pr.received_by, pr.notes
        FROM purchase_receipts pr
        JOIN purchase_order_items poi ON poi.id = pr.purchase_order_item_id
        JOIN medicines ON medicines.id = poi.medicine_id
        JOIN batches ON batches.id = pr.batch_id
        JOIN locations ON locations.id = batches.location_id
        WHERE poi.purchase_order_id = %s
        ORDER BY pr.received_date
        """,
        (purchase_order_id,),
    ).fetchall()

    return {
        "order": order,
        "items": items,
        "receipts": receipts,
        "totals": {
            "order_value": round(sum(i["quantity_ordered"] * i["unit_cost"] for i in items), 2),
            "received_value": round(sum(i["quantity_received"] * i["unit_cost"] for i in items), 2),
            "units_ordered": sum(i["quantity_ordered"] for i in items),
            "units_received": sum(i["quantity_received"] for i in items),
        },
    }


@router.put("/purchase-orders/{purchase_order_id}")
def update_purchase_order(purchase_order_id: int, body: PurchaseOrderUpdate,
                          user: CurrentUser = Depends(require("purchasing.write")),
                          conn: psycopg.Connection = Depends(get_db)):
    order = _lock_order(conn, purchase_order_id)
    notes = blank_to_none(body.notes)
    conn.execute("UPDATE purchase_orders SET notes = %s WHERE id = %s", (notes, purchase_order_id))
    if notes != order["notes"]:
        audit.record(conn, user, "UPDATE", "purchase_order", purchase_order_id,
                     {"notes": order["notes"]}, {"notes": notes})
    conn.commit()
    return {"id": purchase_order_id, "notes": notes}


@router.post("/purchase-orders/{purchase_order_id}/cancel")
def cancel_purchase_order(purchase_order_id: int, body: CancelRequest,
                          user: CurrentUser = Depends(require("purchasing.write")),
                          conn: psycopg.Connection = Depends(get_db)):
    """Cancel an open order. Stock already received stays in inventory; the
    outstanding balance will no longer be received."""
    order = _lock_order(conn, purchase_order_id)
    if order["status"] not in OPEN_STATUSES:
        raise HTTPException(status_code=400, detail=f"A {order['status']} order cannot be cancelled")

    conn.execute("UPDATE purchase_orders SET status = 'CANCELLED' WHERE id = %s", (purchase_order_id,))
    audit.record(conn, user, "CANCEL", "purchase_order", purchase_order_id,
                 {"status": order["status"]}, {"status": "CANCELLED", "reason": body.reason})
    notifications.event(
        conn, category="PURCHASING", severity="WARNING",
        title=f"Purchase order {order['order_number']} cancelled",
        message=f"{user.full_name} cancelled {order['order_number']} ({order['supplier']}): {body.reason}",
        entity_type="purchase_order", entity_id=purchase_order_id, key=f"po:cancelled:{purchase_order_id}",
    )
    conn.commit()
    return {"id": purchase_order_id, "status": "CANCELLED", "previous_status": order["status"]}


# ------------------------------------------------------------
# Purchase order items
# ------------------------------------------------------------

@router.post("/purchase-orders/{purchase_order_id}/items")
def add_purchase_order_item(purchase_order_id: int, item: PurchaseOrderItemCreate,
                            user: CurrentUser = Depends(require("purchasing.write")),
                            conn: psycopg.Connection = Depends(get_db)):
    order = _lock_order(conn, purchase_order_id)
    if order["status"] in ("RECEIVED", "CANCELLED"):
        raise HTTPException(status_code=400, detail="Items cannot be added to a received or cancelled order")

    medicine = conn.execute(
        "SELECT id, name, strength, dosage_form, is_active FROM medicines WHERE id = %s", (item.medicine_id,)
    ).fetchone()
    if medicine is None:
        raise HTTPException(status_code=404, detail="Medicine not found")
    if not medicine["is_active"]:
        raise HTTPException(status_code=400, detail="This medicine is inactive (discontinued) and cannot be ordered")

    if conn.execute(
        "SELECT 1 FROM purchase_order_items WHERE purchase_order_id = %s AND medicine_id = %s",
        (purchase_order_id, item.medicine_id),
    ).fetchone():
        raise HTTPException(status_code=409, detail="This medicine is already on the order; edit that line instead")

    row = conn.execute(
        """
        INSERT INTO purchase_order_items (purchase_order_id, medicine_id, quantity_ordered, unit_cost)
        VALUES (%s, %s, %s, %s)
        RETURNING id, purchase_order_id, medicine_id, quantity_ordered, unit_cost, quantity_received
        """,
        (purchase_order_id, item.medicine_id, item.quantity_ordered, item.unit_cost),
    ).fetchone()

    # Original behaviour: the first item moves a DRAFT order to ORDERED.
    if order["status"] == "DRAFT":
        conn.execute("UPDATE purchase_orders SET status = 'ORDERED' WHERE id = %s", (purchase_order_id,))

    audit.record(conn, user, "ADD_ITEM", "purchase_order", purchase_order_id, None, dict(row))
    conn.commit()
    return {
        "id": row["id"], "purchase_order_id": row["purchase_order_id"], "medicine_id": row["medicine_id"],
        "medicine": medicine["name"], "strength": medicine["strength"], "dosage_form": medicine["dosage_form"],
        "quantity_ordered": row["quantity_ordered"], "unit_cost": float(row["unit_cost"]),
        "quantity_received": row["quantity_received"],
    }


@router.get("/purchase-orders/{purchase_order_id}/items")
def get_purchase_order_items(purchase_order_id: int, user: CurrentUser = Depends(require("inventory.read")),
                             conn: psycopg.Connection = Depends(get_db)):
    rows = conn.execute(
        """
        SELECT poi.id, poi.purchase_order_id, medicines.id AS medicine_id, medicines.name AS medicine,
               medicines.strength, medicines.dosage_form, poi.quantity_ordered, poi.unit_cost,
               poi.quantity_received
        FROM purchase_order_items poi
        JOIN medicines ON poi.medicine_id = medicines.id
        WHERE poi.purchase_order_id = %s
        ORDER BY poi.id
        """,
        (purchase_order_id,),
    ).fetchall()
    return [
        {**r, "unit_cost": float(r["unit_cost"]), "quantity_remaining": r["quantity_ordered"] - r["quantity_received"]}
        for r in rows
    ]


def _lock_item(conn, purchase_order_id: int, item_id: int) -> tuple[dict, dict]:
    order = _lock_order(conn, purchase_order_id)
    item = conn.execute(
        "SELECT * FROM purchase_order_items WHERE id = %s AND purchase_order_id = %s FOR UPDATE",
        (item_id, purchase_order_id),
    ).fetchone()
    if item is None:
        raise HTTPException(status_code=404, detail="Purchase order item not found")
    if order["status"] in ("RECEIVED", "CANCELLED"):
        raise HTTPException(status_code=400, detail=f"Items of a {order['status']} order cannot be changed")
    return order, item


@router.put("/purchase-orders/{purchase_order_id}/items/{item_id}")
def update_purchase_order_item(purchase_order_id: int, item_id: int, body: PurchaseOrderItemUpdate,
                               user: CurrentUser = Depends(require("purchasing.write")),
                               conn: psycopg.Connection = Depends(get_db)):
    order, item = _lock_item(conn, purchase_order_id, item_id)
    if body.quantity_ordered < item["quantity_received"]:
        raise HTTPException(
            status_code=400,
            detail=f"Quantity ordered cannot be less than the {item['quantity_received']} units already received",
        )
    if item["quantity_received"] > 0 and float(item["unit_cost"]) != body.unit_cost:
        raise HTTPException(status_code=400, detail="Unit cost cannot change after stock has been received")

    row = conn.execute(
        """
        UPDATE purchase_order_items SET quantity_ordered = %s, unit_cost = %s WHERE id = %s
        RETURNING id, quantity_ordered, unit_cost, quantity_received
        """,
        (body.quantity_ordered, body.unit_cost, item_id),
    ).fetchone()
    status = _recompute_status(conn, purchase_order_id) if item["quantity_received"] > 0 else order["status"]
    before, after = audit.changed_fields(
        {"quantity_ordered": item["quantity_ordered"], "unit_cost": item["unit_cost"]},
        {"quantity_ordered": row["quantity_ordered"], "unit_cost": row["unit_cost"]},
    )
    if after:
        audit.record(conn, user, "UPDATE_ITEM", "purchase_order", purchase_order_id,
                     {"item_id": item_id, **before}, {"item_id": item_id, **after})
    conn.commit()
    return {**row, "unit_cost": float(row["unit_cost"]), "purchase_order_status": status}


@router.delete("/purchase-orders/{purchase_order_id}/items/{item_id}")
def delete_purchase_order_item(purchase_order_id: int, item_id: int,
                               user: CurrentUser = Depends(require("purchasing.write")),
                               conn: psycopg.Connection = Depends(get_db)):
    order, item = _lock_item(conn, purchase_order_id, item_id)
    if item["quantity_received"] > 0:
        raise HTTPException(status_code=400, detail="A line with received stock cannot be removed")

    conn.execute("DELETE FROM purchase_order_items WHERE id = %s", (item_id,))
    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM purchase_order_items WHERE purchase_order_id = %s", (purchase_order_id,)
    ).fetchone()["n"]
    status = _recompute_status(conn, purchase_order_id) if remaining else order["status"]
    audit.record(conn, user, "REMOVE_ITEM", "purchase_order", purchase_order_id, dict(item), None)
    conn.commit()
    return {"deleted": item_id, "purchase_order_status": status}


# ------------------------------------------------------------
# Receiving
# ------------------------------------------------------------

@router.post("/purchase-receipts")
def receive_purchase_order_item(receipt: PurchaseReceiptCreate, request: Request,
                                user: CurrentUser = Depends(require("purchasing.receive")),
                                conn: psycopg.Connection = Depends(get_db)):
    replay = idempotency.begin(conn, user, request, receipt)
    if replay:
        return replay
    item = conn.execute(
        """
        SELECT poi.id, poi.purchase_order_id, poi.medicine_id, poi.quantity_ordered,
               poi.quantity_received, poi.unit_cost, po.status, po.order_number, po.supplier_id,
               po.order_date, medicines.name AS medicine
        FROM purchase_order_items poi
        JOIN purchase_orders po ON poi.purchase_order_id = po.id
        JOIN medicines ON medicines.id = poi.medicine_id
        WHERE poi.id = %s
        FOR UPDATE OF poi, po
        """,
        (receipt.purchase_order_item_id,),
    ).fetchone()
    if item is None:
        raise HTTPException(status_code=404, detail="Purchase order item not found")

    if item["status"] == "CANCELLED":
        raise HTTPException(status_code=400, detail="Cannot receive stock for a cancelled purchase order")

    remaining = item["quantity_ordered"] - item["quantity_received"]
    if receipt.quantity_received > remaining:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "Received quantity exceeds outstanding quantity",
                "quantity_ordered": item["quantity_ordered"],
                "quantity_already_received": item["quantity_received"],
                "quantity_remaining": remaining,
            },
        )

    if receipt.expiry_date < date.today():
        raise HTTPException(status_code=400, detail="Cannot receive stock that has already expired")

    location_id = receipt.location_id or default_location_id(conn)
    check_location(conn, location_id)
    unit_cost = item["unit_cost"]

    existing = conn.execute(
        """
        SELECT id, quantity, expiry_date, unit_cost FROM batches
        WHERE medicine_id = %s AND batch_number = %s AND location_id = %s
        FOR UPDATE
        """,
        (item["medicine_id"], receipt.batch_number, location_id),
    ).fetchone()

    if existing:
        if existing["expiry_date"] != receipt.expiry_date:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Existing batch has a different expiry date",
                    "existing_expiry_date": str(existing["expiry_date"]),
                    "provided_expiry_date": str(receipt.expiry_date),
                },
            )
        batch_id = existing["id"]
        new_batch_quantity = existing["quantity"] + receipt.quantity_received
        # Weighted average cost. If the batch already holds units of unknown
        # cost, the combined cost stays unknown rather than being guessed.
        if existing["quantity"] == 0:
            new_cost = unit_cost
        elif existing["unit_cost"] is None:
            new_cost = None
        else:
            new_cost = round(
                (existing["quantity"] * existing["unit_cost"] + receipt.quantity_received * unit_cost)
                / new_batch_quantity, 2,
            )
        conn.execute(
            "UPDATE batches SET quantity = %s, unit_cost = %s, supplier_id = COALESCE(supplier_id, %s) WHERE id = %s",
            (new_batch_quantity, new_cost, item["supplier_id"], batch_id),
        )
    else:
        batch_id = conn.execute(
            """
            INSERT INTO batches (medicine_id, batch_number, quantity, expiry_date, location_id,
                                 unit_cost, supplier_id, received_date, purchase_date, barcode_data)
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_DATE, %s, %s)
            RETURNING id
            """,
            (item["medicine_id"], receipt.batch_number, receipt.quantity_received, receipt.expiry_date,
             location_id, unit_cost, item["supplier_id"], item["order_date"],
             blank_to_none(receipt.barcode_data)),
        ).fetchone()["id"]
        new_batch_quantity = receipt.quantity_received

    receipt_row = conn.execute(
        """
        INSERT INTO purchase_receipts
            (purchase_order_item_id, batch_id, quantity_received, received_by, notes, received_by_user_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, received_date
        """,
        (item["id"], batch_id, receipt.quantity_received, receipt.received_by or user.full_name,
         blank_to_none(receipt.notes), user.id),
    ).fetchone()

    movement = stock.insert_movement(conn, batch_id, "RECEIVED", receipt.quantity_received,
                                     "Purchase order received", user)

    conn.execute(
        "UPDATE purchase_order_items SET quantity_received = quantity_received + %s WHERE id = %s",
        (receipt.quantity_received, item["id"]),
    )
    new_status = _recompute_status(conn, item["purchase_order_id"])

    audit.record(conn, user, "RECEIVE", "purchase_order", item["purchase_order_id"], None, {
        "receipt_id": receipt_row["id"], "item_id": item["id"], "batch_id": batch_id,
        "batch_number": receipt.batch_number, "quantity": receipt.quantity_received,
        "expiry_date": receipt.expiry_date, "order_status": new_status,
    })
    notifications.event(
        conn, category="RECEIVING", severity="INFO",
        title=f"Stock received: {item['medicine']} ({item['order_number']})",
        message=(f"{receipt.quantity_received} units of {item['medicine']} batch {receipt.batch_number} "
                 f"received by {user.full_name}. Order is now {new_status.replace('_', ' ').lower()}."),
        entity_type="purchase_order", entity_id=item["purchase_order_id"],
        key=f"receipt:{receipt_row['id']}",
    )
    result = {
        "message": "Stock received successfully",
        "receipt_id": receipt_row["id"],
        "purchase_order_item_id": item["id"],
        "purchase_order_id": item["purchase_order_id"],
        "batch_id": batch_id,
        "quantity_received": receipt.quantity_received,
        "new_batch_quantity": new_batch_quantity,
        "movement_id": movement["id"],
        "movement_type": "RECEIVED",
        "purchase_order_status": new_status,
    }
    idempotency.finish(conn, request, result)
    conn.commit()
    return result


@router.get("/purchase-receipts")
def get_purchase_receipts(purchase_order_id: int | None = None,
                          user: CurrentUser = Depends(require("inventory.read")),
                          conn: psycopg.Connection = Depends(get_db)):
    rows = conn.execute(
        """
        SELECT pr.id, po.id AS purchase_order_id, po.order_number, suppliers.name AS supplier,
               medicines.name AS medicine, medicines.strength, medicines.dosage_form,
               batches.batch_number, pr.quantity_received, pr.received_date, pr.received_by, pr.notes,
               pr.batch_id, batches.expiry_date, poi.unit_cost, locations.name AS location
        FROM purchase_receipts pr
        JOIN purchase_order_items poi ON pr.purchase_order_item_id = poi.id
        JOIN purchase_orders po ON poi.purchase_order_id = po.id
        JOIN suppliers ON po.supplier_id = suppliers.id
        JOIN medicines ON poi.medicine_id = medicines.id
        JOIN batches ON pr.batch_id = batches.id
        JOIN locations ON locations.id = batches.location_id
        WHERE (%(po)s::int IS NULL OR po.id = %(po)s::int)
        ORDER BY pr.received_date DESC
        """,
        {"po": purchase_order_id},
    ).fetchall()
    return [{**r, "received_date": str(r["received_date"])} for r in rows]
