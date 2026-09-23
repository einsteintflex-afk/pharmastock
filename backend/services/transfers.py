# ============================================================
# STOCK TRANSFERS AND REQUISITIONS
# ============================================================
#   REQUESTED -> APPROVED -> DISPATCHED -> RECEIVED
#        \-> REJECTED    (REQUESTED / APPROVED) -> CANCELLED
#
# A TRANSFER is initiated by the sending side; a REQUISITION by the
# receiving side (a ward or department asking the store). Both follow the
# same approval workflow. Dispatch allocates FEFO from the source location
# and writes TRANSFER_OUT movements; receipt writes TRANSFER_IN movements
# into matching batches (same number and expiry) at the destination.
#
# Location scoping: a user assigned to a location (users.location_id) can
# only request stock into it (requisition) or out of it (transfer), dispatch
# from it and receive into it. Managers and administrators are not scoped.

import psycopg
from fastapi import HTTPException

from .. import audit
from ..security import CurrentUser
from . import app_settings, notifications, stock

OPEN_STATUSES = ("REQUESTED", "APPROVED", "DISPATCHED")
UNSCOPED_ROLES = ("ADMINISTRATOR", "MANAGER")


def _scoped_location(user: CurrentUser) -> int | None:
    return None if user.role in UNSCOPED_ROLES else user.location_id


def _require_location(user: CurrentUser, location_id: int, action: str) -> None:
    scoped = _scoped_location(user)
    if scoped is not None and scoped != location_id:
        raise HTTPException(status_code=403,
                            detail=f"You are assigned to another location and cannot {action} here")


def next_number(conn: psycopg.Connection, request_type: str) -> str:
    prefix = "REQ" if request_type == "REQUISITION" else "TRF"
    row = conn.execute(
        """
        SELECT COUNT(*) + 1 AS n FROM transfers
        WHERE request_type = %s AND date_trunc('year', requested_at) = date_trunc('year', CURRENT_DATE)
        """,
        (request_type,),
    ).fetchone()
    year = conn.execute("SELECT to_char(CURRENT_DATE, 'YYYY') AS y").fetchone()["y"]
    number = row["n"]
    while True:
        candidate = f"{prefix}-{year}-{number:05d}"
        if not conn.execute("SELECT 1 FROM transfers WHERE transfer_number = %s", (candidate,)).fetchone():
            return candidate
        number += 1


def _location(conn, location_id: int) -> dict:
    row = conn.execute("SELECT id, name, location_type, is_active FROM locations WHERE id = %s",
                       (location_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Location not found")
    if not row["is_active"]:
        raise HTTPException(status_code=400, detail=f"Location {row['name']} is inactive")
    return row


def create(conn: psycopg.Connection, user: CurrentUser, *, request_type: str, from_location_id: int,
           to_location_id: int, items: list[dict], priority: str, notes: str | None) -> dict:
    if from_location_id == to_location_id:
        raise HTTPException(status_code=400, detail="Source and destination must be different locations")
    source = _location(conn, from_location_id)
    destination = _location(conn, to_location_id)
    # Requisitions are raised by the receiving side, transfers by the sender.
    _require_location(user, to_location_id if request_type == "REQUISITION" else from_location_id, "request stock")

    medicine_ids = [item["medicine_id"] for item in items]
    if len(set(medicine_ids)) != len(medicine_ids):
        raise HTTPException(status_code=400, detail="Each medicine can appear only once; combine the quantities")
    found = conn.execute("SELECT id, is_active FROM medicines WHERE id = ANY(%s)", (medicine_ids,)).fetchall()
    if len(found) != len(medicine_ids):
        raise HTTPException(status_code=404, detail="Medicine not found")

    transfer = conn.execute(
        """
        INSERT INTO transfers (transfer_number, request_type, priority, from_location_id, to_location_id,
                               notes, requested_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (next_number(conn, request_type), request_type, priority, from_location_id, to_location_id,
         notes, user.id),
    ).fetchone()
    for item in items:
        conn.execute(
            "INSERT INTO transfer_items (transfer_id, medicine_id, quantity_requested) VALUES (%s, %s, %s)",
            (transfer["id"], item["medicine_id"], item["quantity"]),
        )
    audit.record(conn, user, f"CREATE_{request_type}", "transfer", transfer["id"], None, {
        "transfer_number": transfer["transfer_number"], "from": source["name"], "to": destination["name"],
        "items": items, "priority": priority,
    })
    label = "Requisition" if request_type == "REQUISITION" else "Transfer request"
    notifications.open_item(
        conn, category="TRANSFERS", severity="WARNING" if priority == "URGENT" else "INFO",
        title=f"{label} {transfer['transfer_number']} awaiting approval",
        message=f"{destination['name']} ← {source['name']}: {len(items)} item(s), requested by {user.full_name}.",
        entity_type="transfer", entity_id=transfer["id"], key=f"transfer:{transfer['id']}:requested",
    )
    return transfer


def _lock(conn, transfer_id: int) -> dict:
    row = conn.execute("SELECT * FROM transfers WHERE id = %s FOR UPDATE", (transfer_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Transfer not found")
    return row


def _expect(transfer: dict, *statuses: str) -> None:
    if transfer["status"] not in statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Transfer is {transfer['status'].lower()}; this action needs status "
                   f"{' or '.join(s.lower() for s in statuses)}",
        )


def _set_status(conn, transfer_id: int, status: str, fields: dict) -> dict:
    assignments = ", ".join(f"{key} = %({key})s" for key in fields)
    return conn.execute(
        f"UPDATE transfers SET status = %(status)s{', ' + assignments if assignments else ''} "
        "WHERE id = %(id)s RETURNING *",
        {"status": status, "id": transfer_id, **fields},
    ).fetchone()


def approve(conn: psycopg.Connection, user: CurrentUser, transfer_id: int,
            quantities: dict[int, int] | None, note: str | None) -> dict:
    transfer = _lock(conn, transfer_id)
    _expect(transfer, "REQUESTED")
    if transfer["requested_by"] == user.id and app_settings.get_all(conn).get("transfers.separate_approver"):
        raise HTTPException(status_code=403, detail="Another user must approve your own request")
    items = conn.execute("SELECT id, quantity_requested FROM transfer_items WHERE transfer_id = %s",
                         (transfer_id,)).fetchall()
    quantities = quantities or {}
    unknown = set(quantities) - {i["id"] for i in items}
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown transfer item(s): {sorted(unknown)}")
    approved = {}
    for item in items:
        quantity = quantities.get(item["id"], item["quantity_requested"])
        if quantity < 0:
            raise HTTPException(status_code=400, detail="Approved quantity cannot be negative")
        approved[item["id"]] = quantity
        conn.execute("UPDATE transfer_items SET quantity_approved = %s WHERE id = %s", (quantity, item["id"]))
    if not any(approved.values()):
        raise HTTPException(status_code=400, detail="Approve at least one unit, or reject the request")
    row = _set_status(conn, transfer_id, "APPROVED",
                      {"approved_by": user.id, "approved_at": _now(conn), "approval_note": note})
    audit.record(conn, user, "APPROVE", "transfer", transfer_id, {"status": "REQUESTED"},
                 {"status": "APPROVED", "quantities": approved, "note": note})
    notifications.resolve(conn, f"transfer:{transfer_id}:requested")
    return row


def reject(conn: psycopg.Connection, user: CurrentUser, transfer_id: int, reason: str) -> dict:
    transfer = _lock(conn, transfer_id)
    _expect(transfer, "REQUESTED")
    row = _set_status(conn, transfer_id, "REJECTED",
                      {"approved_by": user.id, "approved_at": _now(conn), "closed_reason": reason})
    audit.record(conn, user, "REJECT", "transfer", transfer_id, {"status": "REQUESTED"},
                 {"status": "REJECTED", "reason": reason})
    notifications.resolve(conn, f"transfer:{transfer_id}:requested")
    return row


def cancel(conn: psycopg.Connection, user: CurrentUser, transfer_id: int, reason: str) -> dict:
    transfer = _lock(conn, transfer_id)
    _expect(transfer, "REQUESTED", "APPROVED")
    if transfer["requested_by"] != user.id and not user.can("transfers.approve"):
        raise HTTPException(status_code=403, detail="Only the requester or an approver can cancel this request")
    row = _set_status(conn, transfer_id, "CANCELLED", {"closed_reason": reason})
    audit.record(conn, user, "CANCEL", "transfer", transfer_id, {"status": transfer["status"]},
                 {"status": "CANCELLED", "reason": reason})
    notifications.resolve(conn, f"transfer:{transfer_id}:requested")
    return row


def dispatch(conn: psycopg.Connection, user: CurrentUser, transfer_id: int) -> dict:
    """Take the approved quantities from the source location, FEFO."""
    transfer = _lock(conn, transfer_id)
    _expect(transfer, "APPROVED")
    _require_location(user, transfer["from_location_id"], "dispatch stock")
    items = conn.execute(
        """
        SELECT ti.id, ti.medicine_id, ti.quantity_approved, medicines.name AS medicine
        FROM transfer_items ti JOIN medicines ON medicines.id = ti.medicine_id
        WHERE ti.transfer_id = %s AND ti.quantity_approved > 0
        ORDER BY ti.id
        """,
        (transfer_id,),
    ).fetchall()

    reason = f"Transfer {transfer['transfer_number']}"
    dispatched = []
    for item in items:
        plan = stock.fefo_plan(conn, item["medicine_id"], item["quantity_approved"],
                               transfer["from_location_id"], lock=True)
        if plan["shortfall"] > 0:
            raise HTTPException(status_code=400, detail={
                "message": f"Insufficient usable stock of {item['medicine']} at the source location",
                "medicine_id": item["medicine_id"], "requested": item["quantity_approved"],
                "available_usable_stock": plan["available_usable_stock"],
            })
        for allocation in plan["allocations"]:
            conn.execute("UPDATE batches SET quantity = quantity - %s WHERE id = %s",
                         (allocation["allocate"], allocation["batch_id"]))
            movement = stock.insert_movement(conn, allocation["batch_id"], "TRANSFER_OUT", allocation["allocate"],
                                             reason, user, transfer_id=transfer_id)
            conn.execute(
                """
                INSERT INTO transfer_allocations (transfer_item_id, source_batch_id, quantity, out_movement_id)
                VALUES (%s, %s, %s, %s)
                """,
                (item["id"], allocation["batch_id"], allocation["allocate"], movement["id"]),
            )
            dispatched.append({"medicine_id": item["medicine_id"], "batch_id": allocation["batch_id"],
                               "batch_number": allocation["batch_number"], "quantity": allocation["allocate"]})
        conn.execute("UPDATE transfer_items SET quantity_dispatched = %s WHERE id = %s",
                     (item["quantity_approved"], item["id"]))

    row = _set_status(conn, transfer_id, "DISPATCHED", {"dispatched_by": user.id, "dispatched_at": _now(conn)})
    audit.record(conn, user, "DISPATCH", "transfer", transfer_id, {"status": "APPROVED"},
                 {"status": "DISPATCHED", "batches": dispatched})
    notifications.open_item(
        conn, category="TRANSFERS", severity="INFO",
        title=f"{transfer['transfer_number']} dispatched",
        message=f"{sum(d['quantity'] for d in dispatched)} units in transit; receive them at the destination.",
        entity_type="transfer", entity_id=transfer_id, key=f"transfer:{transfer_id}:dispatched",
    )
    return row


def receive(conn: psycopg.Connection, user: CurrentUser, transfer_id: int) -> dict:
    """Book every dispatched unit into the destination location."""
    transfer = _lock(conn, transfer_id)
    _expect(transfer, "DISPATCHED")
    _require_location(user, transfer["to_location_id"], "receive stock")
    allocations = conn.execute(
        """
        SELECT ta.id, ta.transfer_item_id, ta.quantity, source.medicine_id, source.batch_number,
               source.expiry_date, source.unit_cost, source.supplier_id, source.received_date,
               source.purchase_date
        FROM transfer_allocations ta
        JOIN transfer_items ti ON ti.id = ta.transfer_item_id
        JOIN batches source ON source.id = ta.source_batch_id
        WHERE ti.transfer_id = %s
        ORDER BY ta.id
        """,
        (transfer_id,),
    ).fetchall()

    reason = f"Transfer {transfer['transfer_number']}"
    received = []
    for allocation in allocations:
        destination = conn.execute(
            """
            SELECT id, quantity, expiry_date, unit_cost FROM batches
            WHERE medicine_id = %s AND batch_number = %s AND location_id = %s
            FOR UPDATE
            """,
            (allocation["medicine_id"], allocation["batch_number"], transfer["to_location_id"]),
        ).fetchone()
        if destination and destination["expiry_date"] != allocation["expiry_date"]:
            raise HTTPException(status_code=400, detail=(
                f"Batch {allocation['batch_number']} already exists at the destination with a different "
                "expiry date; correct the batch record first"))
        if destination:
            quantity = destination["quantity"] + allocation["quantity"]
            cost = _merged_cost(destination["quantity"], destination["unit_cost"],
                                allocation["quantity"], allocation["unit_cost"])
            conn.execute("UPDATE batches SET quantity = %s, unit_cost = %s WHERE id = %s",
                         (quantity, cost, destination["id"]))
            batch_id = destination["id"]
        else:
            batch_id = conn.execute(
                """
                INSERT INTO batches (medicine_id, batch_number, quantity, expiry_date, location_id, unit_cost,
                                     supplier_id, received_date, purchase_date)
                VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_DATE, %s)
                RETURNING id
                """,
                (allocation["medicine_id"], allocation["batch_number"], allocation["quantity"],
                 allocation["expiry_date"], transfer["to_location_id"], allocation["unit_cost"],
                 allocation["supplier_id"], allocation["purchase_date"]),
            ).fetchone()["id"]
        movement = stock.insert_movement(conn, batch_id, "TRANSFER_IN", allocation["quantity"], reason, user,
                                         transfer_id=transfer_id)
        conn.execute("UPDATE transfer_allocations SET destination_batch_id = %s, in_movement_id = %s WHERE id = %s",
                     (batch_id, movement["id"], allocation["id"]))
        conn.execute("UPDATE transfer_items SET quantity_received = quantity_received + %s WHERE id = %s",
                     (allocation["quantity"], allocation["transfer_item_id"]))
        received.append({"batch_id": batch_id, "batch_number": allocation["batch_number"],
                         "quantity": allocation["quantity"]})

    row = _set_status(conn, transfer_id, "RECEIVED", {"received_by": user.id, "received_at": _now(conn)})
    audit.record(conn, user, "RECEIVE", "transfer", transfer_id, {"status": "DISPATCHED"},
                 {"status": "RECEIVED", "batches": received})
    notifications.resolve(conn, f"transfer:{transfer_id}:dispatched")
    return row


def _merged_cost(existing_qty, existing_cost, added_qty, added_cost):
    if existing_qty == 0:
        return added_cost
    if existing_cost is None or added_cost is None:
        return None
    return round((existing_qty * existing_cost + added_qty * added_cost) / (existing_qty + added_qty), 2)


def _now(conn):
    return conn.execute("SELECT CURRENT_TIMESTAMP::timestamp AS now").fetchone()["now"]


LIST_SQL = """
    SELECT t.id, t.transfer_number, t.request_type, t.priority, t.status,
           t.from_location_id, source.name AS from_location, t.to_location_id,
           destination.name AS to_location, destination.location_type AS to_location_type,
           t.notes, t.approval_note, t.requested_at, requester.full_name AS requested_by_name, t.approved_at,
           approver.full_name AS approved_by_name, t.dispatched_at, dispatcher.full_name AS dispatched_by_name,
           t.received_at, receiver.full_name AS received_by_name, t.closed_reason, t.requested_by,
           (SELECT COUNT(*) FROM transfer_items ti WHERE ti.transfer_id = t.id) AS items,
           (SELECT COALESCE(SUM(ti.quantity_requested), 0) FROM transfer_items ti
             WHERE ti.transfer_id = t.id)::int AS units_requested,
           COUNT(*) OVER () AS total_count
    FROM transfers t
    JOIN locations source ON source.id = t.from_location_id
    JOIN locations destination ON destination.id = t.to_location_id
    LEFT JOIN users requester ON requester.id = t.requested_by
    LEFT JOIN users approver ON approver.id = t.approved_by
    LEFT JOIN users dispatcher ON dispatcher.id = t.dispatched_by
    LEFT JOIN users receiver ON receiver.id = t.received_by
"""


def detail(conn: psycopg.Connection, transfer_id: int) -> dict:
    transfer = conn.execute(LIST_SQL + " WHERE t.id = %s", (transfer_id,)).fetchone()
    if transfer is None:
        raise HTTPException(status_code=404, detail="Transfer not found")
    transfer.pop("total_count")
    items = conn.execute(
        """
        SELECT ti.id, ti.medicine_id, medicines.name AS medicine, medicines.strength, medicines.dosage_form,
               ti.quantity_requested, ti.quantity_approved, ti.quantity_dispatched, ti.quantity_received
        FROM transfer_items ti JOIN medicines ON medicines.id = ti.medicine_id
        WHERE ti.transfer_id = %s ORDER BY ti.id
        """,
        (transfer_id,),
    ).fetchall()
    allocations = conn.execute(
        """
        SELECT ta.transfer_item_id, ta.quantity, ta.source_batch_id, source.batch_number, source.expiry_date,
               ta.destination_batch_id, ta.out_movement_id, ta.in_movement_id
        FROM transfer_allocations ta
        JOIN transfer_items ti ON ti.id = ta.transfer_item_id
        JOIN batches source ON source.id = ta.source_batch_id
        WHERE ti.transfer_id = %s ORDER BY ta.id
        """,
        (transfer_id,),
    ).fetchall()
    for item in items:
        item["batches"] = [a for a in allocations if a["transfer_item_id"] == item["id"]]
        # Units available at the source now, to help the approver.
        item["available_at_source"] = sum(
            b["quantity"] for b in stock.fefo_batches(conn, item["medicine_id"], transfer["from_location_id"])
        )
    return {"transfer": transfer, "items": items}
