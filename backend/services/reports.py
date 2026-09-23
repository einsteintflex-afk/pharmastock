# ============================================================
# REPORTS
# ============================================================
# Each report returns a Report: title, column definitions, rows and summary
# lines. The same Report is rendered as JSON, CSV, Excel or PDF by
# services/exporters.py, so every format shows identical figures.

from dataclasses import dataclass, field
from datetime import date, timedelta

import psycopg
from fastapi import HTTPException

from . import analytics, inventory

# column type: text | int | money | number | date | datetime
Column = tuple[str, str, str]


@dataclass
class Report:
    key: str
    title: str
    columns: list[Column]
    rows: list[dict]
    subtitle: str = ""
    summary: list[tuple[str, str]] = field(default_factory=list)


def _money(value) -> str:
    return f"{float(value or 0):,.2f}"


def _period(date_from: date | None, date_to: date | None, default_days: int = 30) -> tuple[date, date]:
    date_to = date_to or date.today()
    date_from = date_from or (date_to - timedelta(days=default_days - 1))
    if date_from > date_to:
        raise HTTPException(status_code=400, detail="date_from must be on or before date_to")
    return date_from, date_to


BATCH_COLUMNS: list[Column] = [
    ("medicine", "Medicine", "text"),
    ("strength", "Strength", "text"),
    ("dosage_form", "Form", "text"),
    ("batch_number", "Batch", "text"),
    ("location", "Location", "text"),
    ("quantity", "Qty", "int"),
    ("expiry_date", "Expiry", "date"),
    ("days_until_expiry", "Days left", "int"),
    ("status", "Status", "text"),
    ("unit_cost", "Unit cost", "money"),
    ("stock_value", "Value", "money"),
]


def _value_summary(rows, currency) -> list[tuple[str, str]]:
    units = sum(r["quantity"] for r in rows)
    value = sum(float(r["stock_value"] or 0) for r in rows)
    no_cost = sum(r["quantity"] for r in rows if r["unit_cost"] is None)
    summary = [("Batches", str(len(rows))), ("Units", f"{units:,}"), ("Value", f"{currency}{_money(value)}")]
    if no_cost:
        summary.append(("Units with no recorded cost (not valued)", f"{no_cost:,}"))
    return summary


def inventory_report(conn, currency, location_id=None, status=None, **_) -> Report:
    rows = inventory.batches(conn, location_id=location_id, status=status, include_empty=False)
    return Report("inventory", "Inventory Report", BATCH_COLUMNS, rows,
                  subtitle="All batches currently in stock, in FEFO order",
                  summary=_value_summary(rows, currency))


def expiry_report(conn, currency, location_id=None, **_) -> Report:
    rows = [r for r in inventory.batches(conn, location_id=location_id, include_empty=False)
            if r["status"] in ("CRITICAL", "URGENT", "APPROACHING EXPIRY")]
    return Report("expiry", "Expiry Report", BATCH_COLUMNS, rows,
                  subtitle="Batches in stock that are approaching expiry (not yet expired)",
                  summary=_value_summary(rows, currency))


def expired_report(conn, currency, location_id=None, **_) -> Report:
    rows = inventory.batches(conn, location_id=location_id, status="EXPIRED", include_empty=False)
    return Report("expired", "Expired Stock Report", BATCH_COLUMNS, rows,
                  subtitle="Expired batches still held in stock (awaiting write-off/disposal)",
                  summary=_value_summary(rows, currency))


def low_stock_report(conn, currency, **_) -> Report:
    rows = [r for r in analytics.reorder_recommendations(conn)
            if r["stock_status"] != inventory.NORMAL or r["reorder_recommended"]]
    columns = [
        ("medicine", "Medicine", "text"), ("strength", "Strength", "text"),
        ("dosage_form", "Form", "text"), ("usable_stock", "Usable stock", "int"),
        ("reorder_level", "Reorder level", "int"), ("stock_status", "Status", "text"),
        ("average_daily_consumption", "Daily use", "number"), ("days_of_stock", "Days of stock", "number"),
        ("on_order", "On order", "int"), ("recommended_quantity", "Suggested order", "int"),
        ("reason", "Reason", "text"),
    ]
    return Report("low-stock", "Low Stock Report", columns, rows,
                  subtitle="Medicines at/below reorder level or recommended for reorder",
                  summary=[("Medicines", str(len(rows))),
                           ("Out of stock", str(sum(1 for r in rows if r["stock_status"] == inventory.OUT_OF_STOCK)))])


def movement_report(conn, currency, date_from=None, date_to=None, movement_type=None, location_id=None, **_) -> Report:
    date_from, date_to = _period(date_from, date_to)
    rows = conn.execute(
        """
        SELECT sm.movement_date, medicines.name AS medicine, medicines.strength, batches.batch_number,
               locations.name AS location, sm.movement_type, sm.quantity, sm.reason,
               users.full_name AS user_name
        FROM stock_movements sm
        JOIN batches ON batches.id = sm.batch_id
        JOIN medicines ON medicines.id = batches.medicine_id
        JOIN locations ON locations.id = batches.location_id
        LEFT JOIN users ON users.id = sm.user_id
        WHERE sm.movement_date >= %(f)s AND sm.movement_date < %(t)s
          AND (%(type)s::text IS NULL OR sm.movement_type = %(type)s::text)
          AND (%(loc)s::int IS NULL OR batches.location_id = %(loc)s::int)
        ORDER BY sm.movement_date, sm.id
        """,
        {"f": date_from, "t": date_to + timedelta(days=1), "type": movement_type, "loc": location_id},
    ).fetchall()
    columns = [
        ("movement_date", "Date", "datetime"), ("medicine", "Medicine", "text"),
        ("strength", "Strength", "text"), ("batch_number", "Batch", "text"), ("location", "Location", "text"),
        ("movement_type", "Type", "text"), ("quantity", "Qty", "int"), ("reason", "Reason", "text"),
        ("user_name", "User", "text"),
    ]
    totals = {}
    for r in rows:
        totals[r["movement_type"]] = totals.get(r["movement_type"], 0) + r["quantity"]
    return Report("stock-movements", "Stock Movement Report", columns, rows,
                  subtitle=f"{date_from} to {date_to}",
                  summary=[("Movements", str(len(rows)))] + [(k, f"{v:,}") for k, v in sorted(totals.items())])


def purchase_report(conn, currency, date_from=None, date_to=None, supplier_id=None, **_) -> Report:
    date_from, date_to = _period(date_from, date_to, 90)
    rows = conn.execute(
        """
        SELECT po.order_number, po.order_date, suppliers.name AS supplier, po.status,
               COUNT(poi.id) AS items,
               COALESCE(SUM(poi.quantity_ordered), 0)::int AS units_ordered,
               COALESCE(SUM(poi.quantity_received), 0)::int AS units_received,
               COALESCE(SUM(poi.quantity_ordered * poi.unit_cost), 0) AS order_value,
               COALESCE(SUM(poi.quantity_received * poi.unit_cost), 0) AS received_value
        FROM purchase_orders po
        JOIN suppliers ON suppliers.id = po.supplier_id
        LEFT JOIN purchase_order_items poi ON poi.purchase_order_id = po.id
        WHERE po.order_date BETWEEN %(f)s AND %(t)s
          AND (%(s)s::int IS NULL OR po.supplier_id = %(s)s::int)
        GROUP BY po.id, suppliers.name
        ORDER BY po.order_date, po.id
        """,
        {"f": date_from, "t": date_to, "s": supplier_id},
    ).fetchall()
    columns = [
        ("order_number", "Order", "text"), ("order_date", "Date", "date"), ("supplier", "Supplier", "text"),
        ("status", "Status", "text"), ("items", "Lines", "int"), ("units_ordered", "Units ordered", "int"),
        ("units_received", "Units received", "int"), ("order_value", "Order value", "money"),
        ("received_value", "Received value", "money"),
    ]
    live = [r for r in rows if r["status"] != "CANCELLED"]
    return Report("purchases", "Purchase Report", columns, rows, subtitle=f"{date_from} to {date_to}",
                  summary=[("Orders", str(len(rows))),
                           ("Ordered value (excl. cancelled)", f"{currency}{_money(sum(float(r['order_value']) for r in live))}"),
                           ("Received value", f"{currency}{_money(sum(float(r['received_value']) for r in rows))}")])


def supplier_report(conn, currency, **_) -> Report:
    patterns = {p["supplier_id"]: p for p in analytics.purchasing_patterns(conn)["by_supplier"]}
    suppliers = conn.execute("SELECT id, name, contact_person, phone, email, is_active FROM suppliers ORDER BY name").fetchall()
    rows = []
    for s in suppliers:
        p = patterns.get(s["id"], {})
        rows.append({**s, "status": "Active" if s["is_active"] else "Inactive",
                     "orders": p.get("orders", 0), "ordered_value": p.get("ordered_value", 0),
                     "received_value": p.get("received_value", 0),
                     "fill_rate_percent": p.get("fill_rate_percent"),
                     "average_lead_time_days": p.get("average_lead_time_days")})
    columns = [
        ("name", "Supplier", "text"), ("contact_person", "Contact", "text"), ("phone", "Phone", "text"),
        ("email", "Email", "text"), ("status", "Status", "text"), ("orders", "Orders", "int"),
        ("ordered_value", "Ordered value", "money"), ("received_value", "Received value", "money"),
        ("fill_rate_percent", "Fill rate %", "number"), ("average_lead_time_days", "Avg lead time (days)", "number"),
    ]
    return Report("suppliers", "Supplier Report", columns, rows,
                  subtitle="Supplier activity (cancelled orders excluded)",
                  summary=[("Suppliers", str(len(rows))), ("Active", str(sum(1 for r in rows if r["is_active"])))])


def valuation_report(conn, currency, location_id=None, **_) -> Report:
    rows = [r for r in inventory.medicine_stock(conn, location_id=location_id) if r["total_stock"] > 0]
    value = analytics.valuation(conn)
    columns = [
        ("medicine", "Medicine", "text"), ("strength", "Strength", "text"), ("dosage_form", "Form", "text"),
        ("usable_stock", "Usable units", "int"), ("expired_stock", "Expired units", "int"),
        ("total_stock", "Total units", "int"), ("stock_value", "Value at cost", "money"),
        ("units_without_cost", "Units without cost", "int"),
    ]
    summary = [("Total value", f"{currency}{_money(sum(float(r['stock_value']) for r in rows))}")]
    if location_id is None:
        summary.append(("Expired stock value", f"{currency}{_money(value['expired']['value'])}"))
        for status in ("CRITICAL", "URGENT"):
            summary.append((f"{status.title()} expiry value", f"{currency}{_money(value['by_expiry_status'][status]['value'])}"))
        for loc in value["by_location"]:
            summary.append((f"Location: {loc['location']}", f"{currency}{_money(loc['value'])}"))
    return Report("valuation", "Stock Valuation Report", columns, rows,
                  subtitle="Stock valued at batch acquisition cost", summary=summary)


def expiry_loss_report(conn, currency, date_from=None, date_to=None, **_) -> Report:
    date_from, date_to = _period(date_from, date_to, 365)
    rows = conn.execute(
        """
        SELECT sm.movement_date, medicines.name AS medicine, medicines.strength, batches.batch_number,
               batches.expiry_date, sm.movement_type, sm.quantity, batches.unit_cost,
               CASE WHEN batches.unit_cost IS NULL THEN NULL
                    ELSE ROUND(sm.quantity * batches.unit_cost, 2) END AS loss_value,
               sm.reason, users.full_name AS user_name
        FROM stock_movements sm
        JOIN batches ON batches.id = sm.batch_id
        JOIN medicines ON medicines.id = batches.medicine_id
        LEFT JOIN users ON users.id = sm.user_id
        WHERE sm.movement_type IN ('EXPIRED', 'DAMAGED')
          AND sm.movement_date >= %(f)s AND sm.movement_date < %(t)s
        ORDER BY sm.movement_date
        """,
        {"f": date_from, "t": date_to + timedelta(days=1)},
    ).fetchall()
    pending = inventory.batches(conn, status="EXPIRED", include_empty=False)
    columns = [
        ("movement_date", "Written off", "datetime"), ("medicine", "Medicine", "text"),
        ("strength", "Strength", "text"), ("batch_number", "Batch", "text"), ("expiry_date", "Expiry", "date"),
        ("movement_type", "Type", "text"), ("quantity", "Qty", "int"), ("unit_cost", "Unit cost", "money"),
        ("loss_value", "Loss", "money"), ("reason", "Reason", "text"), ("user_name", "User", "text"),
    ]
    expired_loss = sum(float(r["loss_value"] or 0) for r in rows if r["movement_type"] == "EXPIRED")
    damaged_loss = sum(float(r["loss_value"] or 0) for r in rows if r["movement_type"] == "DAMAGED")
    return Report("expiry-loss", "Expiry Loss Report", columns, rows, subtitle=f"Write-offs {date_from} to {date_to}",
                  summary=[("Expiry write-off loss", f"{currency}{_money(expired_loss)}"),
                           ("Damage write-off loss", f"{currency}{_money(damaged_loss)}"),
                           ("Expired stock still on shelf (not yet written off)",
                            f"{sum(p['quantity'] for p in pending):,} units, "
                            f"{currency}{_money(sum(float(p['stock_value'] or 0) for p in pending))}")])


def consumption_report(conn, currency, **_) -> Report:
    rows = analytics.consumption_summary(conn)
    rows.sort(key=lambda r: -r["units_dispensed_last_90_days"])
    columns = [
        ("medicine", "Medicine", "text"), ("strength", "Strength", "text"), ("dosage_form", "Form", "text"),
        ("units_dispensed_last_30_days", "30 days", "int"), ("units_dispensed_last_90_days", "90 days", "int"),
        ("average_daily_consumption", "Daily avg", "number"), ("average_weekly_consumption", "Weekly avg", "number"),
        ("trend", "Trend", "text"), ("usable_stock", "Usable stock", "int"),
        ("days_of_stock", "Days of stock", "number"), ("movement_class", "Class", "text"),
    ]
    return Report("consumption", "Consumption Report", columns, rows,
                  subtitle="Units dispensed (DISPENSED movements)",
                  summary=[("Units dispensed, 30 days", f"{sum(r['units_dispensed_last_30_days'] for r in rows):,}"),
                           ("Units dispensed, 90 days", f"{sum(r['units_dispensed_last_90_days'] for r in rows):,}")])


def expiry_risk_report(conn, currency, **_) -> Report:
    rows = analytics.expiry_risk(conn, include_normal=False)
    columns = [
        ("medicine", "Medicine", "text"), ("batch_number", "Batch", "text"), ("location", "Location", "text"),
        ("quantity", "Qty", "int"), ("expiry_date", "Expiry", "date"), ("days_until_expiry", "Days left", "int"),
        ("average_daily_consumption", "Daily use", "number"),
        ("projected_units_at_risk", "Units at risk", "int"), ("value_at_risk", "Value at risk", "money"),
        ("risk_level", "Risk", "text"),
    ]
    return Report("expiry-risk", "Expiry Risk Report", columns, rows,
                  subtitle="Stock projected to remain unused at expiry at current consumption (FEFO order)",
                  summary=[("Batches at risk", str(len(rows))),
                           ("Units at risk", f"{sum(r['projected_units_at_risk'] for r in rows):,}"),
                           ("Value at risk", f"{currency}{_money(sum(r['value_at_risk'] or 0 for r in rows))}")])


def dispensing_report(conn, currency, date_from=None, date_to=None, **_) -> Report:
    from . import dispensing

    date_from, date_to = _period(date_from, date_to, 1)
    rows = dispensing.search(conn, date_from=date_from, date_to=date_to, limit=100_000)
    rows.reverse()  # oldest first for a daily sheet
    columns = [
        ("dispensation_number", "Number", "text"), ("dispensed_at", "Time", "datetime"),
        ("dispense_type", "Type", "text"), ("prescription_number", "Rx no.", "text"),
        ("medicines", "Medicines", "text"), ("units", "Units", "int"),
        ("payment_method", "Payment", "text"), ("total_amount", "Total", "money"),
        ("status", "Status", "text"), ("dispensed_by", "Dispensed by", "text"),
    ]
    completed = [r for r in rows if r["status"] == "COMPLETED"]
    by_method: dict[str, float] = {}
    for r in completed:
        by_method[r["payment_method"]] = by_method.get(r["payment_method"], 0) + float(r["total_amount"])
    return Report("dispensing", "Dispensing Report", columns, rows, subtitle=f"{date_from} to {date_to}",
                  summary=[("Dispensations", str(len(completed))),
                           ("Voided", str(len(rows) - len(completed))),
                           ("Units dispensed", f"{sum(r['units'] for r in completed):,}"),
                           ("Total sales", f"{currency}{_money(sum(by_method.values()))}")]
                  + [(f"  {k.replace('_', ' ').title()}", f"{currency}{_money(v)}") for k, v in sorted(by_method.items())])


REPORTS = {
    "dispensing": ("Dispensing (daily sales)", dispensing_report, ["date_from", "date_to"]),
    "inventory": ("Inventory", inventory_report, ["location_id", "status"]),
    "expiry": ("Expiry (approaching)", expiry_report, ["location_id"]),
    "expired": ("Expired stock", expired_report, ["location_id"]),
    "low-stock": ("Low stock & reorder", low_stock_report, []),
    "stock-movements": ("Stock movements", movement_report, ["date_from", "date_to", "movement_type", "location_id"]),
    "purchases": ("Purchases", purchase_report, ["date_from", "date_to", "supplier_id"]),
    "suppliers": ("Suppliers", supplier_report, []),
    "valuation": ("Stock valuation", valuation_report, ["location_id"]),
    "expiry-loss": ("Expiry loss", expiry_loss_report, ["date_from", "date_to"]),
    "consumption": ("Consumption", consumption_report, []),
    "expiry-risk": ("Expiry risk", expiry_risk_report, []),
}


def build(conn: psycopg.Connection, key: str, currency: str, **filters) -> Report:
    if key not in REPORTS:
        raise HTTPException(status_code=404, detail="Unknown report")
    return REPORTS[key][1](conn, currency, **filters)
