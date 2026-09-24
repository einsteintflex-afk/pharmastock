# ============================================================
# STOCK COUNTS (physical inventory)
# ============================================================
# IN_PROGRESS -> SUBMITTED -> POSTED   (or CANCELLED before posting)
#
# Counting: each line records the batch's recorded quantity AT THE MOMENT it
# was counted (system_quantity) and the counted quantity. Posting turns
# every difference into a PHYSICAL_COUNT stock adjustment of
# (counted - system_quantity), applied as a delta, so a sale made between
# counting a shelf and posting the count is not overwritten.
#
# Posting needs stock.approve. When any line's difference is above the
# adjustment approval thresholds, the person posting must not be the person
# who submitted the count (separation of duties).

import psycopg
from fastapi import HTTPException

from .. import audit
from ..security import CurrentUser, check_location_scope
from . import adjustments


def _count(conn, count_id: int, lock: bool = False) -> dict:
    row = conn.execute(f"SELECT * FROM stock_counts WHERE id = %s {'FOR UPDATE' if lock else ''}",
                       (count_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Stock count not found")
    return row


def create(conn: psycopg.Connection, user: CurrentUser, location_id: int, name: str | None,
           notes: str | None) -> dict:
    if conn.execute("SELECT 1 FROM locations WHERE id = %s AND is_active", (location_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Location not found")
    check_location_scope(user, location_id)
    number = conn.execute("SELECT nextval(pg_get_serial_sequence('stock_counts', 'id')) AS id").fetchone()["id"]
    conn.execute(
        "INSERT INTO stock_counts (id, count_number, location_id, name, notes, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (number, f"CNT-{number:05d}", location_id, name, notes, user.id),
    )
    audit.record(conn, user, "STOCK_COUNT_CREATED", "stock_count", number, None,
                 {"location_id": location_id, "name": name})
    return detail(conn, number)


def detail(conn: psycopg.Connection, count_id: int) -> dict:
    count = conn.execute(
        """
        SELECT c.*, l.name AS location, cu.full_name AS created_by_name, su.full_name AS submitted_by_name,
               pu.full_name AS posted_by_name
        FROM stock_counts c JOIN locations l ON l.id = c.location_id
        JOIN users cu ON cu.id = c.created_by
        LEFT JOIN users su ON su.id = c.submitted_by LEFT JOIN users pu ON pu.id = c.posted_by
        WHERE c.id = %s
        """,
        (count_id,),
    ).fetchone()
    if count is None:
        raise HTTPException(status_code=404, detail="Stock count not found")
    lines = conn.execute(
        """
        SELECT cl.id, cl.batch_id, cl.medicine_id, m.name AS medicine, m.strength, m.dosage_form, b.batch_number,
               b.expiry_date, cl.system_quantity, cl.counted_quantity,
               cl.counted_quantity - cl.system_quantity AS variance,
               b.unit_cost,
               CASE WHEN b.unit_cost IS NULL THEN NULL
                    ELSE round((cl.counted_quantity - cl.system_quantity) * b.unit_cost, 2) END AS variance_value,
               b.quantity AS current_quantity, cl.reason_code, cl.notes, cl.counted_at, u.full_name AS counted_by_name,
               cl.adjustment_id
        FROM stock_count_lines cl
        JOIN batches b ON b.id = cl.batch_id JOIN medicines m ON m.id = cl.medicine_id
        JOIN users u ON u.id = cl.counted_by
        WHERE cl.stock_count_id = %s
        ORDER BY m.name, b.expiry_date
        """,
        (count_id,),
    ).fetchall()
    # Batches with recorded stock at the location that are not counted yet.
    uncounted = conn.execute(
        """
        SELECT b.id AS batch_id, m.name AS medicine, m.strength, b.batch_number, b.expiry_date, b.quantity
        FROM batches b JOIN medicines m ON m.id = b.medicine_id
        WHERE b.location_id = %s AND b.quantity > 0
          AND NOT EXISTS (SELECT 1 FROM stock_count_lines cl WHERE cl.stock_count_id = %s AND cl.batch_id = b.id)
        ORDER BY m.name, b.expiry_date
        """,
        (count["location_id"], count_id),
    ).fetchall()
    with_variance = [line for line in lines if line["variance"]]
    known_values = [line["variance_value"] for line in with_variance if line["variance_value"] is not None]
    summary = {
        "lines": len(lines),
        "lines_with_variance": len(with_variance),
        "units_over": sum(line["variance"] for line in with_variance if line["variance"] > 0),
        "units_short": -sum(line["variance"] for line in with_variance if line["variance"] < 0),
        "net_variance_value": round(float(sum(known_values)), 2) if known_values else 0.0,
        "lines_without_cost": sum(1 for line in with_variance if line["variance_value"] is None),
        "uncounted_batches": len(uncounted),
    }
    return {**count, "lines": lines, "uncounted": uncounted, "summary": summary}


def record_line(conn: psycopg.Connection, user: CurrentUser, count_id: int, batch_id: int, counted_quantity: int,
                notes: str | None, mode: str = "set") -> dict:
    """Count one batch. mode "set" replaces the counted quantity; "add" adds
    to it (scanning pack by pack)."""
    count = _count(conn, count_id, lock=True)
    if count["status"] != "IN_PROGRESS":
        raise HTTPException(status_code=409, detail="This count is no longer open for counting")
    check_location_scope(user, count["location_id"])
    batch = conn.execute("SELECT id, medicine_id, location_id, quantity FROM batches WHERE id = %s FOR SHARE",
                         (batch_id,)).fetchone()
    if batch is None:
        raise HTTPException(status_code=404, detail="Batch not found")
    if batch["location_id"] != count["location_id"]:
        raise HTTPException(status_code=400, detail="This batch is recorded at a different location")
    if counted_quantity < 0:
        raise HTTPException(status_code=400, detail="Counted quantity cannot be negative")
    existing = conn.execute("SELECT id, counted_quantity FROM stock_count_lines WHERE stock_count_id = %s "
                            "AND batch_id = %s", (count_id, batch_id)).fetchone()
    if existing:
        quantity = existing["counted_quantity"] + counted_quantity if mode == "add" else counted_quantity
        # The recorded quantity is taken again: the physical count is "now".
        conn.execute(
            """
            UPDATE stock_count_lines SET counted_quantity = %s, system_quantity = %s, notes = COALESCE(%s, notes),
                   counted_by = %s, counted_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (quantity, batch["quantity"], notes, user.id, existing["id"]),
        )
    else:
        conn.execute(
            """
            INSERT INTO stock_count_lines (stock_count_id, batch_id, medicine_id, system_quantity, counted_quantity,
                                           notes, counted_by)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (count_id, batch_id, batch["medicine_id"], batch["quantity"], counted_quantity, notes, user.id),
        )
    return detail(conn, count_id)


def remove_line(conn, user: CurrentUser, count_id: int, line_id: int) -> dict:
    count = _count(conn, count_id, lock=True)
    if count["status"] != "IN_PROGRESS":
        raise HTTPException(status_code=409, detail="This count is no longer open for counting")
    if conn.execute("DELETE FROM stock_count_lines WHERE id = %s AND stock_count_id = %s RETURNING id",
                    (line_id, count_id)).fetchone() is None:
        raise HTTPException(status_code=404, detail="Line not found")
    return detail(conn, count_id)


def submit(conn, user: CurrentUser, count_id: int) -> dict:
    count = _count(conn, count_id, lock=True)
    if count["status"] != "IN_PROGRESS":
        raise HTTPException(status_code=409, detail="Only a count in progress can be submitted")
    if not conn.execute("SELECT 1 FROM stock_count_lines WHERE stock_count_id = %s LIMIT 1", (count_id,)).fetchone():
        raise HTTPException(status_code=400, detail="Count at least one item before submitting")
    conn.execute("UPDATE stock_counts SET status = 'SUBMITTED', submitted_by = %s, submitted_at = CURRENT_TIMESTAMP "
                 "WHERE id = %s", (user.id, count_id))
    audit.record(conn, user, "STOCK_COUNT_SUBMITTED", "stock_count", count_id)
    return detail(conn, count_id)


def reopen(conn, user: CurrentUser, count_id: int) -> dict:
    count = _count(conn, count_id, lock=True)
    if count["status"] != "SUBMITTED":
        raise HTTPException(status_code=409, detail="Only a submitted count can be sent back for recounting")
    conn.execute("UPDATE stock_counts SET status = 'IN_PROGRESS', submitted_by = NULL, submitted_at = NULL "
                 "WHERE id = %s", (count_id,))
    audit.record(conn, user, "STOCK_COUNT_REOPENED", "stock_count", count_id)
    return detail(conn, count_id)


def post(conn, user: CurrentUser, count_id: int) -> dict:
    count = _count(conn, count_id, lock=True)
    if count["status"] != "SUBMITTED":
        raise HTTPException(status_code=409, detail="Only a submitted count can be posted")
    lines = conn.execute(
        """
        SELECT cl.*, b.unit_cost FROM stock_count_lines cl JOIN batches b ON b.id = cl.batch_id
        WHERE cl.stock_count_id = %s AND cl.counted_quantity <> cl.system_quantity ORDER BY cl.id
        """,
        (count_id,),
    ).fetchall()
    large = any(adjustments.needs_approval(conn, line["counted_quantity"] - line["system_quantity"], line["unit_cost"])
                for line in lines)
    if large and count["submitted_by"] == user.id:
        raise HTTPException(status_code=403, detail="Differences above the approval threshold: the count must be "
                                                    "posted by someone other than the person who submitted it")
    posted = 0
    for line in lines:
        adjustment = adjustments.create(
            conn, user, batch_id=line["batch_id"], reason_code="PHYSICAL_COUNT",
            change=line["counted_quantity"] - line["system_quantity"],
            notes=f"Stock count {count['count_number']}" + (f": {line['notes']}" if line["notes"] else ""),
            stock_count_id=count_id, approved=True,
        )
        conn.execute("UPDATE stock_count_lines SET adjustment_id = %s WHERE id = %s", (adjustment["id"], line["id"]))
        posted += 1
    conn.execute("UPDATE stock_counts SET status = 'POSTED', posted_by = %s, posted_at = CURRENT_TIMESTAMP "
                 "WHERE id = %s", (user.id, count_id))
    audit.record(conn, user, "STOCK_COUNT_POSTED", "stock_count", count_id, None, {"adjustments": posted})
    return detail(conn, count_id)


def cancel(conn, user: CurrentUser, count_id: int, reason: str) -> dict:
    count = _count(conn, count_id, lock=True)
    if count["status"] not in ("IN_PROGRESS", "SUBMITTED"):
        raise HTTPException(status_code=409, detail="A posted count cannot be cancelled")
    conn.execute("UPDATE stock_counts SET status = 'CANCELLED', cancelled_reason = %s WHERE id = %s",
                 (reason, count_id))
    audit.record(conn, user, "STOCK_COUNT_CANCELLED", "stock_count", count_id, None, {"reason": reason})
    return detail(conn, count_id)


def search(conn, status: str | None = None, location_id: int | None = None) -> list:
    return conn.execute(
        """
        SELECT c.id, c.count_number, c.name, c.status, c.created_at, c.submitted_at, c.posted_at,
               l.name AS location, c.location_id, u.full_name AS created_by_name,
               COUNT(cl.id) AS lines,
               COUNT(cl.id) FILTER (WHERE cl.counted_quantity <> cl.system_quantity) AS lines_with_variance
        FROM stock_counts c JOIN locations l ON l.id = c.location_id JOIN users u ON u.id = c.created_by
        LEFT JOIN stock_count_lines cl ON cl.stock_count_id = c.id
        WHERE (%(status)s::text IS NULL OR c.status = %(status)s::text)
          AND (%(location)s::int IS NULL OR c.location_id = %(location)s::int)
        GROUP BY c.id, l.name, u.full_name
        ORDER BY c.id DESC LIMIT 200
        """,
        {"status": status, "location": location_id},
    ).fetchall()
