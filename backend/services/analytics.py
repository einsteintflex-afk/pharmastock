# ============================================================
# INVENTORY INTELLIGENCE
# ============================================================
# Low stock and reorder recommendations, consumption, slow movers, expiry
# risk, valuation, forecasting, turnover and purchasing patterns. All
# figures are computed from the live database; every method is described in
# the returned data so users can see how a number was produced.

import math
from datetime import date

import psycopg

from . import app_settings, expiry, inventory


def _settings(conn):
    values = app_settings.get_all(conn)
    return {
        "slow_units": int(values["stock.slow_moving_units_90d"]),
        "lead_time": int(values["reorder.lead_time_days"]),
        "cover_days": int(values["reorder.cover_days"]),
    }


# ------------------------------------------------------------
# Low stock and reorder recommendations
# ------------------------------------------------------------

def reorder_recommendations(conn: psycopg.Connection, *, only_needed: bool = False) -> list[dict]:
    """Per medicine: usable stock, consumption rate, days of stock remaining
    and a reorder recommendation.

    A reorder is recommended when usable stock is at/below the reorder level
    or will run out within the supplier lead time. The quantity brings stock
    (including units already on order) up to
        max(reorder_level, daily_rate * (lead_time + cover_days)).
    """
    cfg = _settings(conn)
    stock = inventory.medicine_stock(conn)
    usage = inventory.consumption(conn)
    on_order = inventory.outstanding_orders(conn)

    results = []
    for item in stock:
        use = usage.get(item["medicine_id"], {})
        daily = use.get("average_daily", 0) or 0
        usable = item["usable_stock"]
        ordered = on_order.get(item["medicine_id"], 0)
        days_of_stock = round(usable / daily, 1) if daily > 0 else None

        below_level = usable <= item["reorder_level"]
        runs_out_in_lead_time = days_of_stock is not None and days_of_stock <= cfg["lead_time"]
        target = max(item["reorder_level"], math.ceil(daily * (cfg["lead_time"] + cfg["cover_days"])))
        shortfall = max(0, target - usable - ordered)

        needed = (below_level or runs_out_in_lead_time) and shortfall > 0
        if needed:
            reasons = []
            if usable == 0:
                reasons.append("out of usable stock")
            elif below_level:
                reasons.append(f"usable stock {usable} is at or below reorder level {item['reorder_level']}")
            if runs_out_in_lead_time:
                reasons.append(f"about {days_of_stock} days of stock left, lead time is {cfg['lead_time']} days")
            reason = "; ".join(reasons)
        elif (below_level or runs_out_in_lead_time) and ordered:
            reason = f"low, but {ordered} units already on order"
        else:
            reason = None

        row = {
            "medicine_id": item["medicine_id"],
            "medicine": item["medicine"],
            "strength": item["strength"],
            "dosage_form": item["dosage_form"],
            "usable_stock": usable,
            "expired_stock": item["expired_stock"],
            "reorder_level": item["reorder_level"],
            "stock_status": item["stock_status"],
            "on_order": ordered,
            "average_daily_consumption": daily,
            "days_of_stock": days_of_stock,
            "target_stock": target,
            "reorder_recommended": needed,
            "recommended_quantity": shortfall if needed else 0,
            "reason": reason,
        }
        if not only_needed or needed:
            results.append(row)

    results.sort(key=lambda r: (not r["reorder_recommended"], r["days_of_stock"] if r["days_of_stock"] is not None else 1e9, r["medicine"]))
    return results


# ------------------------------------------------------------
# Consumption, slow and fast movers
# ------------------------------------------------------------

def consumption_summary(conn: psycopg.Connection) -> list[dict]:
    stock = {s["medicine_id"]: s for s in inventory.medicine_stock(conn)}
    usage = inventory.consumption(conn)
    cfg = _settings(conn)

    rows = []
    for medicine_id, use in usage.items():
        item = stock[medicine_id]
        d90 = use["dispensed_90d"]
        if d90 == 0:
            movement_class = "NO MOVEMENT"
        elif d90 <= cfg["slow_units"]:
            movement_class = "SLOW-MOVING"
        else:
            movement_class = "ACTIVE"
        daily = use["average_daily"]
        rows.append({
            "medicine_id": medicine_id,
            "medicine": item["medicine"],
            "strength": item["strength"],
            "dosage_form": item["dosage_form"],
            "units_dispensed_last_30_days": use["dispensed_30d"],
            "units_dispensed_previous_30_days": use["dispensed_prev_30d"],
            "units_dispensed_last_90_days": d90,
            "dispensing_transactions_last_90_days": use["transactions_90d"],
            "average_daily_consumption": daily,
            "average_weekly_consumption": use["average_weekly"],
            "trend": use["trend"],
            "trend_change_percent": use["trend_change_percent"],
            "last_dispensed": use["last_dispensed"],
            "usable_stock": item["usable_stock"],
            "days_of_stock": round(item["usable_stock"] / daily, 1) if daily > 0 else None,
            "movement_class": movement_class,
        })
    return rows


# ------------------------------------------------------------
# Expiry risk
# ------------------------------------------------------------

def expiry_risk(conn: psycopg.Connection, *, include_normal: bool = True) -> list[dict]:
    """Batch-level expiry risk.

    For each medicine, batches are consumed in FEFO order at the current
    daily consumption rate. A batch's projected units-at-risk are the units
    that will still be on the shelf on its expiry date:

        demand_before_expiry = daily_rate * days_until_expiry
        units_consumed       = clamp(demand_before_expiry - units in earlier batches, 0, qty)
        units_at_risk        = qty - units_consumed

    Risk level: EXPIRED (already expired), HIGH (>= 50% of the batch at
    risk, or any units at risk and expiry within the urgent threshold),
    MEDIUM (some units at risk), LOW (none projected).
    """
    thresholds = app_settings.expiry_thresholds(conn)
    usage = inventory.consumption(conn)
    rows = inventory.batches(conn, include_empty=False)

    by_medicine: dict[int, list[dict]] = {}
    for row in rows:
        by_medicine.setdefault(row["medicine_id"], []).append(row)

    results = []
    for medicine_id, batch_list in by_medicine.items():
        daily = usage.get(medicine_id, {}).get("average_daily", 0) or 0
        earlier_units = 0

        for batch in sorted(batch_list, key=lambda b: (b["expiry_date"], b["batch_id"])):
            qty = batch["quantity"]
            days = batch["days_until_expiry"]

            if days < 0:
                at_risk = qty
                level = "EXPIRED"
            else:
                demand = daily * (days + 1)  # usable through the expiry date
                consumed = min(qty, max(0.0, demand - earlier_units))
                at_risk = int(round(qty - consumed))
                share = at_risk / qty if qty else 0
                if at_risk == 0:
                    level = "LOW"
                elif share >= 0.5 or days <= thresholds.urgent_days:
                    level = "HIGH"
                else:
                    level = "MEDIUM"
                earlier_units += qty

            cost = batch["unit_cost"]
            value_at_risk = round(float(cost) * at_risk, 2) if cost is not None else None

            if include_normal or level != "LOW":
                results.append({
                    "batch_id": batch["batch_id"],
                    "medicine_id": medicine_id,
                    "medicine": batch["medicine"],
                    "strength": batch["strength"],
                    "dosage_form": batch["dosage_form"],
                    "batch_number": batch["batch_number"],
                    "location": batch["location"],
                    "quantity": qty,
                    "expiry_date": batch["expiry_date"],
                    "days_until_expiry": days,
                    "expiry_status": batch["status"],
                    "average_daily_consumption": daily,
                    "projected_units_at_risk": at_risk,
                    "unit_cost": cost,
                    "stock_value": batch["stock_value"],
                    "value_at_risk": value_at_risk,
                    "risk_level": level,
                })

    order = {"EXPIRED": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    results.sort(key=lambda r: (order[r["risk_level"]], -(r["value_at_risk"] or 0), r["days_until_expiry"]))
    return results


# ------------------------------------------------------------
# Valuation
# ------------------------------------------------------------

def valuation(conn: psycopg.Connection) -> dict:
    """Stock value at cost, split by expiry status and by location. Batches
    with no recorded unit cost are counted separately (never guessed)."""
    rows = inventory.batches(conn, include_empty=False)

    def blank():
        return {"units": 0, "value": 0.0, "batches": 0, "units_without_cost": 0}

    total = blank()
    by_status = {status: blank() for status in expiry.STATUS_ORDER}
    by_location: dict[str, dict] = {}

    for row in rows:
        for bucket in (total, by_status[row["status"]], by_location.setdefault(row["location"], blank())):
            bucket["units"] += row["quantity"]
            bucket["batches"] += 1
            if row["unit_cost"] is None:
                bucket["units_without_cost"] += row["quantity"]
            else:
                bucket["value"] = round(bucket["value"] + float(row["stock_value"]), 2)

    return {
        "total": total,
        "usable_value": round(total["value"] - by_status["EXPIRED"]["value"], 2),
        "expired": by_status["EXPIRED"],
        "by_expiry_status": by_status,
        "by_location": [{"location": name, **values} for name, values in sorted(by_location.items())],
    }


# ------------------------------------------------------------
# Demand forecast
# ------------------------------------------------------------

def forecast(conn: psycopg.Connection, horizon_days: int = 30) -> list[dict]:
    """Forecast demand with simple exponential smoothing (alpha 0.5) over the
    last 12 weekly dispensing totals. Returns the forecast for the horizon
    and whether usable stock covers it. With little history the forecast is
    flagged as low confidence."""
    rows = conn.execute(
        """
        WITH weeks AS (
            SELECT generate_series(0, 11) AS weeks_ago
        )
        SELECT medicines.id AS medicine_id, weeks.weeks_ago,
               COALESCE(SUM(sm.quantity), 0)::int AS units
        FROM medicines
        CROSS JOIN weeks
        LEFT JOIN batches ON batches.medicine_id = medicines.id
        LEFT JOIN stock_movements sm
            ON sm.batch_id = batches.id
            AND sm.movement_type = 'DISPENSED'
            AND sm.movement_date >= CURRENT_TIMESTAMP - (weeks.weeks_ago + 1) * INTERVAL '7 days'
            AND sm.movement_date <  CURRENT_TIMESTAMP - weeks.weeks_ago * INTERVAL '7 days'
        GROUP BY medicines.id, weeks.weeks_ago
        ORDER BY medicines.id, weeks.weeks_ago DESC
        """
    ).fetchall()

    series: dict[int, list[int]] = {}
    for row in rows:
        series.setdefault(row["medicine_id"], []).append(row["units"])  # oldest -> newest

    stock = {s["medicine_id"]: s for s in inventory.medicine_stock(conn)}
    results = []
    for medicine_id, weekly in series.items():
        level = None
        for units in weekly:
            level = units if level is None else 0.5 * units + 0.5 * level
        weekly_rate = level or 0.0
        demand = round(weekly_rate / 7 * horizon_days, 1)
        active_weeks = sum(1 for w in weekly if w > 0)
        item = stock[medicine_id]
        results.append({
            "medicine_id": medicine_id,
            "medicine": item["medicine"],
            "strength": item["strength"],
            "dosage_form": item["dosage_form"],
            "weekly_history_oldest_first": weekly,
            "forecast_weekly_demand": round(weekly_rate, 1),
            "forecast_demand": demand,
            "horizon_days": horizon_days,
            "usable_stock": item["usable_stock"],
            "projected_stock_at_horizon": round(item["usable_stock"] - demand, 1),
            "covers_horizon": item["usable_stock"] >= demand,
            "confidence": "LOW" if active_weeks < 4 else ("MEDIUM" if active_weeks < 8 else "HIGH"),
            "method": "Exponential smoothing (alpha 0.5) of 12 weekly dispensing totals",
        })
    results.sort(key=lambda r: (r["covers_horizon"], -r["forecast_demand"]))
    return results


# ------------------------------------------------------------
# Turnover and purchasing patterns
# ------------------------------------------------------------

def turnover(conn: psycopg.Connection) -> list[dict]:
    """Stock turnover over 90 days. Average stock is approximated as the mean
    of current and 90-days-ago stock, reconstructed from the ledger."""
    rows = conn.execute(
        """
        SELECT medicines.id AS medicine_id, medicines.name AS medicine,
               medicines.strength, medicines.dosage_form,
               COALESCE(SUM(batches_total.qty), 0)::int AS current_stock
        FROM medicines
        LEFT JOIN (SELECT medicine_id, SUM(quantity) AS qty FROM batches GROUP BY medicine_id) AS batches_total
            ON batches_total.medicine_id = medicines.id
        GROUP BY medicines.id
        """
    ).fetchall()
    changes = conn.execute(
        """
        SELECT batches.medicine_id,
               COALESCE(SUM(CASE WHEN sm.movement_type IN ('RECEIVED','RETURNED','ADJUSTMENT')
                                 THEN sm.quantity ELSE -sm.quantity END), 0)::int AS net_change_90d,
               COALESCE(SUM(sm.quantity) FILTER (WHERE sm.movement_type = 'DISPENSED'), 0)::int AS dispensed_90d
        FROM stock_movements sm
        JOIN batches ON batches.id = sm.batch_id
        WHERE sm.movement_date >= CURRENT_TIMESTAMP - INTERVAL '90 days'
        GROUP BY batches.medicine_id
        """
    ).fetchall()
    change_map = {c["medicine_id"]: c for c in changes}

    results = []
    for row in rows:
        change = change_map.get(row["medicine_id"], {"net_change_90d": 0, "dispensed_90d": 0})
        start_stock = row["current_stock"] - change["net_change_90d"]
        average = (row["current_stock"] + start_stock) / 2
        ratio = round(change["dispensed_90d"] / average, 2) if average > 0 else None
        results.append({
            **row,
            "stock_90_days_ago": start_stock,
            "average_stock": round(average, 1),
            "dispensed_90d": change["dispensed_90d"],
            "turnover_90d": ratio,
            "annualised_turnover": round(ratio * 365 / 90, 2) if ratio is not None else None,
            "days_of_inventory": round(90 / ratio, 1) if ratio else None,
        })
    results.sort(key=lambda r: (r["turnover_90d"] is None, -(r["turnover_90d"] or 0)))
    return results


def purchasing_patterns(conn: psycopg.Connection) -> dict:
    by_supplier = conn.execute(
        """
        SELECT suppliers.id AS supplier_id, suppliers.name AS supplier,
               COUNT(DISTINCT po.id) AS orders,
               COALESCE(SUM(poi.quantity_ordered * poi.unit_cost), 0) AS ordered_value,
               COALESCE(SUM(poi.quantity_received * poi.unit_cost), 0) AS received_value,
               COALESCE(SUM(poi.quantity_ordered), 0)::int AS units_ordered,
               COALESCE(SUM(poi.quantity_received), 0)::int AS units_received
        FROM suppliers
        LEFT JOIN purchase_orders po ON po.supplier_id = suppliers.id AND po.status <> 'CANCELLED'
        LEFT JOIN purchase_order_items poi ON poi.purchase_order_id = po.id
        GROUP BY suppliers.id
        ORDER BY ordered_value DESC
        """
    ).fetchall()
    lead_times = conn.execute(
        """
        SELECT po.supplier_id,
               ROUND(AVG(first_receipt.received::date - po.order_date), 1) AS average_lead_time_days
        FROM purchase_orders po
        JOIN (
            SELECT poi.purchase_order_id, MIN(pr.received_date) AS received
            FROM purchase_receipts pr
            JOIN purchase_order_items poi ON poi.id = pr.purchase_order_item_id
            GROUP BY poi.purchase_order_id
        ) AS first_receipt ON first_receipt.purchase_order_id = po.id
        GROUP BY po.supplier_id
        """
    ).fetchall()
    lead = {row["supplier_id"]: row["average_lead_time_days"] for row in lead_times}
    monthly = conn.execute(
        """
        SELECT to_char(date_trunc('month', po.order_date), 'YYYY-MM') AS month,
               COUNT(DISTINCT po.id) AS orders,
               COALESCE(SUM(poi.quantity_ordered * poi.unit_cost), 0) AS ordered_value
        FROM purchase_orders po
        LEFT JOIN purchase_order_items poi ON poi.purchase_order_id = po.id
        WHERE po.status <> 'CANCELLED'
        GROUP BY 1 ORDER BY 1
        """
    ).fetchall()
    return {
        "by_supplier": [
            {**row,
             "fill_rate_percent": round(row["units_received"] / row["units_ordered"] * 100, 1)
             if row["units_ordered"] else None,
             "average_lead_time_days": lead.get(row["supplier_id"])}
            for row in by_supplier
        ],
        "by_month": monthly,
    }


# ------------------------------------------------------------
# Dashboard
# ------------------------------------------------------------

def dashboard(conn: psycopg.Connection) -> dict:
    thresholds = app_settings.expiry_thresholds(conn)
    stock = inventory.medicine_stock(conn)
    value = valuation(conn)
    risk = expiry_risk(conn, include_normal=False)
    reorder = reorder_recommendations(conn, only_needed=True)
    movement = consumption_summary(conn)

    status_counts = {s: value["by_expiry_status"][s] for s in expiry.STATUS_ORDER}

    recent = conn.execute(
        """
        SELECT sm.id, sm.batch_id, medicines.name AS medicine, medicines.strength,
               medicines.dosage_form, batches.batch_number, sm.movement_type, sm.quantity,
               sm.movement_date, sm.reason, users.full_name AS user_name
        FROM stock_movements sm
        JOIN batches ON batches.id = sm.batch_id
        JOIN medicines ON medicines.id = batches.medicine_id
        LEFT JOIN users ON users.id = sm.user_id
        ORDER BY sm.movement_date DESC, sm.id DESC
        LIMIT 8
        """
    ).fetchall()

    return {
        "as_of": date.today(),
        "thresholds": thresholds.as_params(),
        "total_medicines": len(stock),
        "medicines_in_stock": sum(1 for s in stock if s["usable_stock"] > 0),
        "total_units": value["total"]["units"],
        "usable_units": sum(s["usable_stock"] for s in stock),
        "stock_value": value["total"]["value"],
        "usable_stock_value": value["usable_value"],
        "units_without_cost": value["total"]["units_without_cost"],
        "expired": status_counts["EXPIRED"],
        "critical": status_counts["CRITICAL"],
        "urgent": status_counts["URGENT"],
        "approaching": status_counts["APPROACHING EXPIRY"],
        "low_stock_count": sum(1 for s in stock if s["stock_status"] == inventory.LOW_STOCK),
        "out_of_stock_count": sum(1 for s in stock if s["stock_status"] == inventory.OUT_OF_STOCK),
        "slow_moving_count": sum(1 for m in movement if m["movement_class"] in ("SLOW-MOVING", "NO MOVEMENT") and m["usable_stock"] > 0),
        "expiry_risk": {
            "batches": len(risk),
            "units_at_risk": sum(r["projected_units_at_risk"] for r in risk),
            "value_at_risk": round(sum(r["value_at_risk"] or 0 for r in risk), 2),
            "top": risk[:6],
        },
        "reorder": reorder[:6],
        "reorder_count": len(reorder),
        "stock_levels": stock,
        "slow_moving": [m for m in movement if m["movement_class"] != "ACTIVE" and m["usable_stock"] > 0][:6],
        "recent_movements": recent,
        "valuation_by_location": value["by_location"],
    }
