# ============================================================
# INVENTORY QUERIES
# ============================================================
# Read-only queries over medicines, batches and movements. Stock figures:
#
#   usable_stock   units in batches that have not expired (dispensable)
#   expired_stock  units in expired batches still on the shelf
#   total_stock    usable + expired (physically present)
#
# Stock status uses usable stock, because expired units cannot be dispensed:
#   OUT OF STOCK  usable_stock = 0
#   LOW STOCK     usable_stock <= reorder_level
#   NORMAL        otherwise

import psycopg

from . import app_settings, expiry

OUT_OF_STOCK = "OUT OF STOCK"
LOW_STOCK = "LOW STOCK"
NORMAL = "NORMAL"


def stock_status_sql(usable: str, reorder: str) -> str:
    return f"""
        CASE
            WHEN {usable} = 0 THEN 'OUT OF STOCK'
            WHEN {usable} <= {reorder} THEN 'LOW STOCK'
            ELSE 'NORMAL'
        END
    """


def batches(
    conn: psycopg.Connection,
    *,
    medicine_id: int | None = None,
    location_id: int | None = None,
    supplier_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    include_empty: bool = True,
    batch_id: int | None = None,
) -> list[dict]:
    """Every batch with medicine, location, supplier, expiry status and value."""
    params = app_settings.expiry_thresholds(conn).as_params()
    where = ["TRUE"]

    if batch_id is not None:
        where.append("batches.id = %(batch_id)s")
        params["batch_id"] = batch_id
    if medicine_id is not None:
        where.append("batches.medicine_id = %(medicine_id)s")
        params["medicine_id"] = medicine_id
    if location_id is not None:
        where.append("batches.location_id = %(location_id)s")
        params["location_id"] = location_id
    if supplier_id is not None:
        where.append("batches.supplier_id = %(supplier_id)s")
        params["supplier_id"] = supplier_id
    if not include_empty:
        where.append("batches.quantity > 0")
    if search:
        where.append("""(
            medicines.name ILIKE %(search)s
            OR batches.batch_number ILIKE %(search)s
            OR medicines.strength ILIKE %(search)s
            OR medicines.dosage_form ILIKE %(search)s
        )""")
        params["search"] = f"%{search.strip()}%"

    status_expr = expiry.status_sql()

    rows = conn.execute(
        f"""
        SELECT * FROM (
            SELECT
                batches.id AS batch_id,
                batches.medicine_id,
                medicines.name AS medicine,
                medicines.strength,
                medicines.dosage_form,
                batches.batch_number,
                batches.quantity,
                batches.expiry_date,
                batches.expiry_date - CURRENT_DATE AS days_until_expiry,
                {status_expr} AS status,
                batches.location_id,
                locations.name AS location,
                locations.location_type,
                batches.supplier_id,
                suppliers.name AS supplier,
                batches.unit_cost,
                batches.received_date,
                batches.created_at,
                CASE WHEN batches.unit_cost IS NULL THEN NULL
                     ELSE ROUND(batches.quantity * batches.unit_cost, 2) END AS stock_value
            FROM batches
            JOIN medicines ON medicines.id = batches.medicine_id
            JOIN locations ON locations.id = batches.location_id
            LEFT JOIN suppliers ON suppliers.id = batches.supplier_id
            WHERE {' AND '.join(where)}
        ) AS inventory
        WHERE (%(status_filter)s::text IS NULL OR inventory.status = %(status_filter)s::text)
        ORDER BY expiry_date, batch_id
        """,
        {**params, "status_filter": status},
    ).fetchall()

    return rows


def medicine_stock(
    conn: psycopg.Connection,
    *,
    medicine_id: int | None = None,
    location_id: int | None = None,
) -> list[dict]:
    """Per-medicine stock summary (all registered medicines, including those
    with no batches)."""
    params = {"medicine_id": medicine_id, "location_id": location_id}

    return conn.execute(
        f"""
        SELECT
            summary.*,
            {stock_status_sql('summary.usable_stock', 'summary.reorder_level')} AS stock_status
        FROM (
            SELECT
                medicines.id AS medicine_id,
                medicines.name AS medicine,
                medicines.strength,
                medicines.dosage_form,
                medicines.reorder_level,
                COALESCE(SUM(batches.quantity) FILTER (WHERE batches.expiry_date >= CURRENT_DATE), 0)::int
                    AS usable_stock,
                COALESCE(SUM(batches.quantity) FILTER (WHERE batches.expiry_date < CURRENT_DATE), 0)::int
                    AS expired_stock,
                COALESCE(SUM(batches.quantity), 0)::int AS total_stock,
                COUNT(batches.id) FILTER (WHERE batches.quantity > 0) AS batches_in_stock,
                MIN(batches.expiry_date) FILTER (
                    WHERE batches.quantity > 0 AND batches.expiry_date >= CURRENT_DATE
                ) AS next_expiry,
                ROUND(COALESCE(SUM(batches.quantity * batches.unit_cost), 0), 2) AS stock_value,
                COALESCE(SUM(batches.quantity) FILTER (
                    WHERE batches.unit_cost IS NULL AND batches.quantity > 0
                ), 0)::int AS units_without_cost
            FROM medicines
            LEFT JOIN batches
                ON batches.medicine_id = medicines.id
                AND (%(location_id)s::int IS NULL OR batches.location_id = %(location_id)s::int)
            WHERE (%(medicine_id)s::int IS NULL OR medicines.id = %(medicine_id)s::int)
            GROUP BY medicines.id
        ) AS summary
        ORDER BY summary.medicine
        """,
        params,
    ).fetchall()


def consumption(conn: psycopg.Connection, medicine_id: int | None = None) -> dict[int, dict]:
    """Units DISPENSED per medicine over the last 30 / 90 days and the 30 days
    before that. Returns {medicine_id: stats}."""
    rows = conn.execute(
        """
        SELECT
            medicines.id AS medicine_id,
            COALESCE(SUM(sm.quantity) FILTER (
                WHERE sm.movement_date >= CURRENT_TIMESTAMP - INTERVAL '30 days'), 0)::int AS dispensed_30d,
            COALESCE(SUM(sm.quantity) FILTER (
                WHERE sm.movement_date >= CURRENT_TIMESTAMP - INTERVAL '60 days'
                  AND sm.movement_date < CURRENT_TIMESTAMP - INTERVAL '30 days'), 0)::int AS dispensed_prev_30d,
            COALESCE(SUM(sm.quantity) FILTER (
                WHERE sm.movement_date >= CURRENT_TIMESTAMP - INTERVAL '90 days'), 0)::int AS dispensed_90d,
            COUNT(sm.id) FILTER (
                WHERE sm.movement_date >= CURRENT_TIMESTAMP - INTERVAL '90 days') AS transactions_90d,
            MAX(sm.movement_date) AS last_dispensed
        FROM medicines
        LEFT JOIN batches ON batches.medicine_id = medicines.id
        LEFT JOIN stock_movements sm
            ON sm.batch_id = batches.id AND sm.movement_type = 'DISPENSED'
        WHERE (%(medicine_id)s::int IS NULL OR medicines.id = %(medicine_id)s::int)
        GROUP BY medicines.id
        """,
        {"medicine_id": medicine_id},
    ).fetchall()

    result = {}
    for row in rows:
        d30, d90, prev = row["dispensed_30d"], row["dispensed_90d"], row["dispensed_prev_30d"]
        # Daily rate: the 30-day window reflects current demand; fall back to
        # the 90-day window when nothing was dispensed recently.
        daily = d30 / 30 if d30 > 0 else d90 / 90
        result[row["medicine_id"]] = {
            **row,
            "average_daily": round(daily, 3),
            "average_weekly": round(daily * 7, 2),
            "trend": _trend(d30, prev),
            "trend_change_percent": (round((d30 - prev) / prev * 100, 1) if prev else None),
        }
    return result


def _trend(current: int, previous: int) -> str:
    if current == 0 and previous == 0:
        return "NO MOVEMENT"
    if previous == 0:
        return "NEW DEMAND"
    change = (current - previous) / previous
    if change >= 0.2:
        return "INCREASING"
    if change <= -0.2:
        return "DECREASING"
    return "STABLE"


def outstanding_orders(conn: psycopg.Connection) -> dict[int, int]:
    """Units ordered but not yet received, per medicine (sent orders only;
    DRAFT orders have not been placed with the supplier)."""
    rows = conn.execute(
        """
        SELECT poi.medicine_id,
               SUM(poi.quantity_ordered - poi.quantity_received)::int AS on_order
        FROM purchase_order_items poi
        JOIN purchase_orders po ON po.id = poi.purchase_order_id
        WHERE po.status IN ('ORDERED', 'PARTIALLY_RECEIVED')
        GROUP BY poi.medicine_id
        """
    ).fetchall()
    return {row["medicine_id"]: row["on_order"] for row in rows}
