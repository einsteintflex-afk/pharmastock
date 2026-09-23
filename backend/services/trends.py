# ============================================================
# TRENDS, STOCK-OUTS, LOCATION AND SUPPLIER ANALYTICS
# ============================================================
# Everything is reconstructed from the stock ledger (stock_movements), which
# records every change to every batch. Internal transfers move stock between
# locations and are excluded from organisation-wide in/out totals; they do
# appear in the location figures.
#
# Reconstructing past stock: closing quantity of a batch on day D =
#   current quantity - sum(signed movements after D).
# Batch hold status (quarantine / recall) is not historised, so past usable
# stock treats every non-expired unit as usable. This is stated in the
# returned "limitations".

from collections import defaultdict
from datetime import date, timedelta

import psycopg

from . import app_settings, inventory, stock

def _month_starts(months: int) -> list[date]:
    first = date.today().replace(day=1)
    starts = []
    for _ in range(months):
        starts.append(first)
        first = (first - timedelta(days=1)).replace(day=1)
    return list(reversed(starts))


def _next_month(day: date) -> date:
    return (day.replace(day=28) + timedelta(days=4)).replace(day=1)


# ------------------------------------------------------------
# Stock trend (monthly)
# ------------------------------------------------------------

def stock_trends(conn: psycopg.Connection, months: int = 12) -> dict:
    """Monthly movement totals (units and value at batch cost) and the stock
    held at the end of each month."""
    starts = _month_starts(months)
    rows = conn.execute(
        f"""
        SELECT date_trunc('month', sm.movement_date)::date AS month, sm.movement_type,
               SUM(sm.quantity)::int AS units,
               SUM({stock.signed_quantity_sql('sm')})::int AS signed_units,
               ROUND(SUM(ABS(sm.quantity) * batches.unit_cost), 2) AS value
        FROM stock_movements sm
        JOIN batches ON batches.id = sm.batch_id
        WHERE sm.movement_date >= %s
        GROUP BY 1, 2
        """,
        (starts[0],),
    ).fetchall()
    current = conn.execute(
        "SELECT COALESCE(SUM(quantity), 0)::int AS units, "
        "ROUND(COALESCE(SUM(quantity * unit_cost), 0), 2) AS value FROM batches"
    ).fetchone()

    by_month = {start: {"month": start.strftime("%Y-%m"), "received": 0, "dispensed": 0, "returned": 0,
                        "damaged": 0, "expired": 0, "adjustment_net": 0, "transfers": 0,
                        "received_value": 0.0, "dispensed_value": 0.0, "written_off_value": 0.0, "net_change": 0}
                for start in starts}
    for row in rows:
        month = by_month.get(row["month"])
        if month is None:
            continue
        kind = row["movement_type"]
        value = float(row["value"] or 0)
        if kind == "ADJUSTMENT":
            month["adjustment_net"] += row["signed_units"]
        elif kind in ("TRANSFER_OUT", "TRANSFER_IN"):
            month["transfers"] += row["units"] if kind == "TRANSFER_OUT" else 0
        else:
            month[kind.lower()] += row["units"]
        if kind == "RECEIVED":
            month["received_value"] += value
        elif kind == "DISPENSED":
            month["dispensed_value"] += value
        elif kind in ("DAMAGED", "EXPIRED"):
            month["written_off_value"] += value
        month["net_change"] += row["signed_units"]

    # Closing stock, walking back from today.
    closing = current["units"]
    series = [by_month[s] for s in starts]
    for month in reversed(series):
        month["closing_units"] = closing
        closing -= month["net_change"]
    for month in series:
        for key in ("received_value", "dispensed_value", "written_off_value"):
            month[key] = round(month[key], 2)
    return {
        "months": series,
        "current_units": current["units"],
        "current_value": float(current["value"]),
        "method": "Monthly totals of ledger movements; closing stock reconstructed backwards from today's "
                  "batch quantities. Values use batch acquisition cost (units without cost are not valued). "
                  "Transfers are internal and do not change organisation totals.",
    }


# ------------------------------------------------------------
# Expiry trend: write-offs by month and the forward expiry calendar
# ------------------------------------------------------------

def expiry_trends(conn: psycopg.Connection, months: int = 12) -> dict:
    starts = _month_starts(months)
    written_off = conn.execute(
        """
        SELECT date_trunc('month', sm.movement_date)::date AS month,
               SUM(sm.quantity)::int AS units, ROUND(SUM(sm.quantity * batches.unit_cost), 2) AS value,
               COUNT(DISTINCT sm.batch_id) AS batches
        FROM stock_movements sm JOIN batches ON batches.id = sm.batch_id
        WHERE sm.movement_type = 'EXPIRED' AND sm.movement_date >= %s
        GROUP BY 1
        """,
        (starts[0],),
    ).fetchall()
    past = {r["month"]: r for r in written_off}

    today = date.today()
    first = today.replace(day=1)
    calendar_starts = [first]
    for _ in range(months - 1):
        calendar_starts.append(_next_month(calendar_starts[-1]))
    upcoming = conn.execute(
        """
        SELECT date_trunc('month', expiry_date)::date AS month, COUNT(*) AS batches,
               SUM(quantity)::int AS units, ROUND(SUM(quantity * unit_cost), 2) AS value
        FROM batches
        WHERE quantity > 0 AND expiry_date >= CURRENT_DATE AND expiry_date < %s
        GROUP BY 1
        """,
        (_next_month(calendar_starts[-1]),),
    ).fetchall()
    future = {r["month"]: r for r in upcoming}
    expired_on_shelf = conn.execute(
        "SELECT COUNT(*) AS batches, COALESCE(SUM(quantity), 0)::int AS units, "
        "ROUND(COALESCE(SUM(quantity * unit_cost), 0), 2) AS value "
        "FROM batches WHERE quantity > 0 AND expiry_date < CURRENT_DATE"
    ).fetchone()
    return {
        "written_off_by_month": [
            {"month": s.strftime("%Y-%m"), "batches": past.get(s, {}).get("batches", 0),
             "units": past.get(s, {}).get("units", 0), "value": float(past.get(s, {}).get("value") or 0)}
            for s in starts
        ],
        "expiring_by_month": [
            {"month": s.strftime("%Y-%m"), "batches": future.get(s, {}).get("batches", 0),
             "units": future.get(s, {}).get("units", 0), "value": float(future.get(s, {}).get("value") or 0)}
            for s in calendar_starts
        ],
        "expired_on_shelf": {k: float(v) if k == "value" else v for k, v in expired_on_shelf.items()},
        "method": "Write-offs are EXPIRED movements; the calendar groups stock currently held by expiry month.",
    }


# ------------------------------------------------------------
# Stock-outs (history reconstructed per batch)
# ------------------------------------------------------------

def stockouts(conn: psycopg.Connection, days: int = 90) -> dict:
    """Days on which each medicine had no usable (non-expired) stock, over the
    last `days` days, reconstructed per batch from the ledger."""
    start = date.today() - timedelta(days=days - 1)
    batches = conn.execute(
        "SELECT id, medicine_id, quantity, expiry_date, created_at::date AS created FROM batches"
    ).fetchall()
    changes = conn.execute(
        f"""
        SELECT sm.batch_id, sm.movement_date::date AS day, SUM({stock.signed_quantity_sql('sm')})::int AS net
        FROM stock_movements sm
        WHERE sm.movement_date >= %s
        GROUP BY 1, 2
        """,
        (start,),
    ).fetchall()
    first_seen = {r["medicine_id"]: r["first_day"] for r in conn.execute(
        """
        SELECT batches.medicine_id, MIN(sm.movement_date)::date AS first_day
        FROM stock_movements sm JOIN batches ON batches.id = sm.batch_id
        GROUP BY batches.medicine_id
        """
    ).fetchall()}

    net = defaultdict(dict)
    for c in changes:
        net[c["batch_id"]][c["day"]] = c["net"]

    days_list = [start + timedelta(days=i) for i in range(days)]
    usable = defaultdict(lambda: [0] * days)
    for batch in batches:
        quantity = batch["quantity"]
        history = net.get(batch["id"], {})
        # Walk backwards: closing quantity on day d, then undo that day's movements.
        for index in range(days - 1, -1, -1):
            day = days_list[index]
            if day <= batch["expiry_date"] and quantity > 0:
                usable[batch["medicine_id"]][index] += quantity
            quantity -= history.get(day, 0)

    medicines = inventory.medicine_stock(conn)
    results = []
    for item in medicines:
        tracked_from = first_seen.get(item["medicine_id"])
        if tracked_from is None:
            continue  # never stocked: not a stock-out
        series = usable[item["medicine_id"]]
        counted = [(days_list[i], series[i]) for i in range(days) if days_list[i] >= tracked_from]
        out_days = [d for d, units in counted if units <= 0]
        episodes = 0
        previous_out = False
        for _, units in counted:
            is_out = units <= 0
            if is_out and not previous_out:
                episodes += 1
            previous_out = is_out
        below = sum(1 for _, units in counted if units <= item["reorder_level"])
        results.append({
            "medicine_id": item["medicine_id"], "medicine": item["medicine"], "strength": item["strength"],
            "dosage_form": item["dosage_form"], "usable_stock": item["usable_stock"],
            "currently_out": item["usable_stock"] == 0,
            "stockout_days": len(out_days), "stockout_episodes": episodes,
            "days_at_or_below_reorder_level": below, "days_observed": len(counted),
            "last_stockout_day": out_days[-1] if out_days else None,
            "availability_percent": round((1 - len(out_days) / len(counted)) * 100, 1) if counted else None,
        })
    results.sort(key=lambda r: (-r["stockout_days"], r["medicine"]))
    return {
        "days": days,
        "medicines": results,
        "summary": {
            "medicines_out_now": sum(1 for r in results if r["currently_out"]),
            "medicines_with_stockouts": sum(1 for r in results if r["stockout_days"]),
            "total_stockout_days": sum(r["stockout_days"] for r in results),
        },
        "limitations": [
            "Past quarantine/recall status is not recorded, so held stock counts as usable in history.",
            "Days before a medicine's first recorded movement are not counted.",
        ],
    }


# ------------------------------------------------------------
# Locations
# ------------------------------------------------------------

def location_analytics(conn: psycopg.Connection) -> list[dict]:
    thresholds = app_settings.expiry_thresholds(conn)
    rows = conn.execute(
        """
        SELECT locations.id AS location_id, locations.name AS location, locations.location_type,
               locations.is_active, parent.name AS parent,
               COUNT(batches.id) FILTER (WHERE batches.quantity > 0) AS batches_in_stock,
               COUNT(DISTINCT batches.medicine_id) FILTER (WHERE batches.quantity > 0) AS medicines_in_stock,
               COALESCE(SUM(batches.quantity), 0)::int AS units,
               COALESCE(SUM(batches.quantity) FILTER (
                   WHERE batches.expiry_date >= CURRENT_DATE AND batches.batch_status = 'ACTIVE'), 0)::int
                   AS usable_units,
               COALESCE(SUM(batches.quantity) FILTER (WHERE batches.expiry_date < CURRENT_DATE), 0)::int
                   AS expired_units,
               ROUND(COALESCE(SUM(batches.quantity * batches.unit_cost), 0), 2) AS stock_value,
               ROUND(COALESCE(SUM(batches.quantity * batches.unit_cost) FILTER (
                   WHERE batches.expiry_date >= CURRENT_DATE
                     AND batches.expiry_date <= CURRENT_DATE + %(urgent)s::int), 0), 2) AS value_expiring_soon
        FROM locations
        LEFT JOIN locations parent ON parent.id = locations.parent_id
        LEFT JOIN batches ON batches.location_id = locations.id
        GROUP BY locations.id, parent.name
        ORDER BY locations.id
        """,
        {"urgent": thresholds.urgent_days},
    ).fetchall()
    flows = conn.execute(
        """
        SELECT batches.location_id,
               COALESCE(SUM(sm.quantity) FILTER (WHERE sm.movement_type = 'DISPENSED'), 0)::int AS dispensed_30d,
               COALESCE(SUM(sm.quantity) FILTER (WHERE sm.movement_type = 'RECEIVED'), 0)::int AS received_30d,
               COALESCE(SUM(sm.quantity) FILTER (WHERE sm.movement_type = 'TRANSFER_IN'), 0)::int AS transferred_in_30d,
               COALESCE(SUM(sm.quantity) FILTER (WHERE sm.movement_type = 'TRANSFER_OUT'), 0)::int AS transferred_out_30d,
               COALESCE(SUM(sm.quantity) FILTER (WHERE sm.movement_type IN ('DAMAGED', 'EXPIRED')), 0)::int
                   AS written_off_30d
        FROM stock_movements sm JOIN batches ON batches.id = sm.batch_id
        WHERE sm.movement_date >= CURRENT_TIMESTAMP - INTERVAL '30 days'
        GROUP BY batches.location_id
        """
    ).fetchall()
    flow = {f["location_id"]: f for f in flows}
    open_transfers = {r["location_id"]: r["n"] for r in conn.execute(
        """
        SELECT location_id, COUNT(*) AS n FROM (
            SELECT to_location_id AS location_id FROM transfers WHERE status IN ('REQUESTED', 'APPROVED', 'DISPATCHED')
            UNION ALL
            SELECT from_location_id FROM transfers WHERE status IN ('REQUESTED', 'APPROVED', 'DISPATCHED')
        ) AS t GROUP BY location_id
        """
    ).fetchall()}
    empty = {"dispensed_30d": 0, "received_30d": 0, "transferred_in_30d": 0, "transferred_out_30d": 0,
             "written_off_30d": 0}
    results = []
    for row in rows:
        f = flow.get(row["location_id"], empty)
        results.append({**row, **{k: f[k] for k in empty}, "open_transfers": open_transfers.get(row["location_id"], 0),
                        "stock_value": float(row["stock_value"]),
                        "value_expiring_soon": float(row["value_expiring_soon"])})
    return results


# ------------------------------------------------------------
# Suppliers
# ------------------------------------------------------------

def supplier_performance(conn: psycopg.Connection, days: int = 365) -> dict:
    rows = conn.execute(
        """
        WITH orders AS (
            SELECT po.id, po.supplier_id, po.order_date, po.status,
                   SUM(poi.quantity_ordered * poi.unit_cost) AS ordered_value,
                   SUM(poi.quantity_received * poi.unit_cost) AS received_value,
                   SUM(poi.quantity_ordered)::int AS units_ordered,
                   SUM(poi.quantity_received)::int AS units_received
            FROM purchase_orders po
            LEFT JOIN purchase_order_items poi ON poi.purchase_order_id = po.id
            WHERE po.order_date >= CURRENT_DATE - %(days)s::int AND po.status <> 'CANCELLED'
            GROUP BY po.id
        ), receipts AS (
            SELECT po.supplier_id, po.id AS order_id, MIN(pr.received_date)::date AS first_receipt,
                   MAX(pr.received_date)::date AS last_receipt, COUNT(pr.id) AS receipts
            FROM purchase_receipts pr
            JOIN purchase_order_items poi ON poi.id = pr.purchase_order_item_id
            JOIN purchase_orders po ON po.id = poi.purchase_order_id
            GROUP BY po.supplier_id, po.id
        )
        SELECT suppliers.id AS supplier_id, suppliers.name AS supplier, suppliers.is_active,
               COUNT(DISTINCT orders.id) AS orders,
               COUNT(DISTINCT orders.id) FILTER (WHERE orders.status IN ('ORDERED', 'PARTIALLY_RECEIVED')) AS open_orders,
               ROUND(COALESCE(SUM(orders.ordered_value), 0), 2) AS ordered_value,
               ROUND(COALESCE(SUM(orders.received_value), 0), 2) AS received_value,
               COALESCE(SUM(orders.units_ordered), 0)::int AS units_ordered,
               COALESCE(SUM(orders.units_received), 0)::int AS units_received,
               MAX(orders.order_date) AS last_order_date,
               (SELECT MAX(r.last_receipt) FROM receipts r WHERE r.supplier_id = suppliers.id) AS last_receipt_date,
               (SELECT ROUND(AVG(r.first_receipt - o.order_date), 1) FROM receipts r
                  JOIN orders o ON o.id = r.order_id WHERE r.supplier_id = suppliers.id) AS average_lead_time_days,
               (SELECT COALESCE(SUM(r.receipts), 0) FROM receipts r WHERE r.supplier_id = suppliers.id)::int AS receipts
        FROM suppliers
        LEFT JOIN orders ON orders.supplier_id = suppliers.id
        GROUP BY suppliers.id
        """,
        {"days": days},
    ).fetchall()
    prices = conn.execute(
        """
        SELECT po.supplier_id, poi.medicine_id, MIN(poi.unit_cost) AS low, MAX(poi.unit_cost) AS high
        FROM purchase_order_items poi JOIN purchase_orders po ON po.id = poi.purchase_order_id
        WHERE po.status <> 'CANCELLED' AND po.order_date >= CURRENT_DATE - %s::int
        GROUP BY 1, 2
        """,
        (days,),
    ).fetchall()
    price_changes = defaultdict(int)
    for p in prices:
        if p["high"] and p["low"] is not None and p["high"] != p["low"]:
            price_changes[p["supplier_id"]] += 1

    total_spend = sum(float(r["ordered_value"]) for r in rows) or 0
    results = []
    for row in rows:
        spend = float(row["ordered_value"])
        results.append({
            **row,
            "ordered_value": spend,
            "received_value": float(row["received_value"]),
            "average_lead_time_days": float(row["average_lead_time_days"]) if row["average_lead_time_days"] is not None else None,
            "fill_rate_percent": round(row["units_received"] / row["units_ordered"] * 100, 1) if row["units_ordered"] else None,
            "share_of_spend_percent": round(spend / total_spend * 100, 1) if total_spend else None,
            "products_with_price_changes": price_changes.get(row["supplier_id"], 0),
        })
    results.sort(key=lambda r: -r["ordered_value"])
    return {
        "period_days": days,
        "suppliers": results,
        "method": "Orders placed in the period (cancelled excluded). Fill rate = units received / units ordered, "
                  "which counts partly received open orders. Lead time = days from order date to first receipt.",
    }


# ------------------------------------------------------------
# Reorder / purchasing trend
# ------------------------------------------------------------

def purchasing_trends(conn: psycopg.Connection, months: int = 12) -> list[dict]:
    starts = _month_starts(months)
    rows = conn.execute(
        """
        SELECT date_trunc('month', po.order_date)::date AS month, COUNT(DISTINCT po.id) AS orders,
               COUNT(poi.id) AS lines, COALESCE(SUM(poi.quantity_ordered), 0)::int AS units_ordered,
               ROUND(COALESCE(SUM(poi.quantity_ordered * poi.unit_cost), 0), 2) AS ordered_value
        FROM purchase_orders po LEFT JOIN purchase_order_items poi ON poi.purchase_order_id = po.id
        WHERE po.status <> 'CANCELLED' AND po.order_date >= %s
        GROUP BY 1
        """,
        (starts[0],),
    ).fetchall()
    received = conn.execute(
        """
        SELECT date_trunc('month', pr.received_date)::date AS month, SUM(pr.quantity_received)::int AS units
        FROM purchase_receipts pr WHERE pr.received_date >= %s GROUP BY 1
        """,
        (starts[0],),
    ).fetchall()
    by = {r["month"]: r for r in rows}
    got = {r["month"]: r["units"] for r in received}
    return [{"month": s.strftime("%Y-%m"), "orders": by.get(s, {}).get("orders", 0),
             "lines": by.get(s, {}).get("lines", 0), "units_ordered": by.get(s, {}).get("units_ordered", 0),
             "ordered_value": float(by.get(s, {}).get("ordered_value") or 0), "units_received": got.get(s, 0)}
            for s in starts]


# ------------------------------------------------------------
# Period summaries (used by the assistant and the dashboard)
# ------------------------------------------------------------

def _month_bounds(month: str | None) -> tuple[date, date]:
    if month:
        try:
            year, number = (int(part) for part in month.split("-"))
            start = date(year, number, 1)
        except (ValueError, TypeError) as error:
            raise ValueError("month must be YYYY-MM") from error
    else:
        start = date.today().replace(day=1)
    return start, _next_month(start)


def stock_changes(conn: psycopg.Connection, date_from: date, date_to_exclusive: date, limit: int = 25) -> dict:
    """Per-medicine movement totals in a period, largest net change first."""
    rows = conn.execute(
        f"""
        SELECT medicines.id AS medicine_id, medicines.name AS medicine, medicines.strength,
               COALESCE(SUM(sm.quantity) FILTER (WHERE sm.movement_type = 'RECEIVED'), 0)::int AS received,
               COALESCE(SUM(sm.quantity) FILTER (WHERE sm.movement_type = 'DISPENSED'), 0)::int AS dispensed,
               COALESCE(SUM(sm.quantity) FILTER (WHERE sm.movement_type = 'RETURNED'), 0)::int AS returned,
               COALESCE(SUM(sm.quantity) FILTER (WHERE sm.movement_type IN ('DAMAGED', 'EXPIRED')), 0)::int
                   AS written_off,
               COALESCE(SUM(sm.quantity) FILTER (WHERE sm.movement_type = 'ADJUSTMENT'), 0)::int AS adjustments,
               COALESCE(SUM({stock.signed_quantity_sql('sm')})
                        FILTER (WHERE sm.movement_type NOT IN ('TRANSFER_IN', 'TRANSFER_OUT')), 0)::int AS net_change
        FROM stock_movements sm
        JOIN batches ON batches.id = sm.batch_id
        JOIN medicines ON medicines.id = batches.medicine_id
        WHERE sm.movement_date >= %s AND sm.movement_date < %s
        GROUP BY medicines.id
        ORDER BY ABS(COALESCE(SUM({stock.signed_quantity_sql('sm')})
                      FILTER (WHERE sm.movement_type NOT IN ('TRANSFER_IN', 'TRANSFER_OUT')), 0)) DESC,
                 medicines.name
        """,
        (date_from, date_to_exclusive),
    ).fetchall()
    return {
        "date_from": date_from, "date_to": date_to_exclusive - timedelta(days=1),
        "medicines_changed": len(rows),
        "totals": {k: sum(r[k] for r in rows) for k in ("received", "dispensed", "returned", "written_off",
                                                        "adjustments", "net_change")},
        "rows": rows[:limit], "truncated": len(rows) > limit,
    }


def monthly_summary(conn: psycopg.Connection, month: str | None = None) -> dict:
    start, end = _month_bounds(month)
    changes = stock_changes(conn, start, end, limit=100_000)
    sales = conn.execute(
        """
        SELECT COUNT(*) FILTER (WHERE status = 'COMPLETED') AS dispensations,
               COALESCE(SUM(total_amount) FILTER (WHERE status = 'COMPLETED'), 0) AS sales_total,
               COUNT(*) FILTER (WHERE status = 'VOIDED') AS voided
        FROM dispensations WHERE dispensed_at >= %s AND dispensed_at < %s
        """,
        (start, end),
    ).fetchone()
    purchases = conn.execute(
        """
        SELECT COUNT(DISTINCT po.id) AS orders,
               ROUND(COALESCE(SUM(poi.quantity_ordered * poi.unit_cost), 0), 2) AS ordered_value
        FROM purchase_orders po LEFT JOIN purchase_order_items poi ON poi.purchase_order_id = po.id
        WHERE po.status <> 'CANCELLED' AND po.order_date >= %s AND po.order_date < %s
        """,
        (start, end),
    ).fetchone()
    losses = conn.execute(
        """
        SELECT sm.movement_type, SUM(sm.quantity)::int AS units, ROUND(SUM(sm.quantity * batches.unit_cost), 2) AS value
        FROM stock_movements sm JOIN batches ON batches.id = sm.batch_id
        WHERE sm.movement_type IN ('EXPIRED', 'DAMAGED') AND sm.movement_date >= %s AND sm.movement_date < %s
        GROUP BY sm.movement_type
        """,
        (start, end),
    ).fetchall()
    transfers_done = conn.execute(
        "SELECT COUNT(*) AS n FROM transfers WHERE status = 'RECEIVED' AND received_at >= %s AND received_at < %s",
        (start, end),
    ).fetchone()["n"]
    top_dispensed = sorted(changes["rows"], key=lambda r: -r["dispensed"])[:5]
    return {
        "month": start.strftime("%Y-%m"),
        "period": {"from": start, "to": end - timedelta(days=1), "complete": end <= date.today()},
        "movements": changes["totals"],
        "medicines_with_movement": changes["medicines_changed"],
        "dispensing": {"dispensations": sales["dispensations"], "voided": sales["voided"],
                       "sales_total": float(sales["sales_total"])},
        "purchasing": {"orders": purchases["orders"], "ordered_value": float(purchases["ordered_value"])},
        "write_offs": {r["movement_type"]: {"units": r["units"], "value": float(r["value"] or 0)} for r in losses},
        "transfers_received": transfers_done,
        "top_dispensed": [{"medicine": r["medicine"], "strength": r["strength"], "units": r["dispensed"]}
                          for r in top_dispensed if r["dispensed"]],
        "largest_net_changes": changes["rows"][:5],
    }


def overstock(conn: psycopg.Connection, min_days_of_stock: int = 180) -> list[dict]:
    """Medicines holding much more stock than they use: days of stock above
    the threshold, or stock with no dispensing in 90 days."""
    from . import analytics

    value = {r["medicine_id"]: r for r in inventory.medicine_stock(conn)}
    rows = []
    for r in analytics.consumption_summary(conn):
        if r["usable_stock"] <= 0:
            continue
        days = r["days_of_stock"]
        if days is None or days >= min_days_of_stock:
            item = value[r["medicine_id"]]
            rows.append({
                "medicine": r["medicine"], "strength": r["strength"], "usable_stock": r["usable_stock"],
                "units_dispensed_last_90_days": r["units_dispensed_last_90_days"],
                "average_daily_consumption": r["average_daily_consumption"], "days_of_stock": days,
                "stock_value": float(item["stock_value"]), "next_expiry": item["next_expiry"],
                "reason": "no dispensing in 90 days" if days is None else f"about {days:.0f} days of stock",
            })
    rows.sort(key=lambda r: -r["stock_value"])
    return rows
