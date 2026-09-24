# ============================================================
# OPERATIONAL INTELLIGENCE: ATTENTION LIST, DAILY BRIEF, MOVERS, SEARCH
# ============================================================
# Everything here is computed from the organization's own records at the
# moment of the request. Nothing is estimated without saying how, and a
# figure that cannot be computed (e.g. value without a recorded cost) is
# reported as unknown rather than guessed.

from datetime import date, timedelta

import psycopg

from ..security import CurrentUser
from . import analytics, inventory, stock


# ------------------------------------------------------------
# Fast, slow and dead movers
# ------------------------------------------------------------

def movers(conn: psycopg.Connection, dead_days: int = 180) -> dict:
    """Fast movers: the medicines that together account for 80 % of units
    dispensed in the last 90 days (Pareto / ABC 'A' class). Slow movers: the
    organization's slow-moving threshold. Dead stock: usable stock with no
    dispensing for `dead_days` days (or never)."""
    rows = analytics.consumption_summary(conn)
    moving = sorted((r for r in rows if r["units_dispensed_last_90_days"] > 0),
                    key=lambda r: -r["units_dispensed_last_90_days"])
    total = sum(r["units_dispensed_last_90_days"] for r in moving)
    fast, running = [], 0
    for row in moving:
        if total and running / total >= 0.8:
            break
        running += row["units_dispensed_last_90_days"]
        fast.append({**row, "share_percent": round(row["units_dispensed_last_90_days"] / total * 100, 1)})
    values = {v["medicine_id"]: v for v in conn.execute(
        """
        SELECT b.medicine_id, SUM(b.quantity) FILTER (WHERE b.expiry_date >= CURRENT_DATE AND b.batch_status = 'ACTIVE')
                   AS usable, SUM(b.quantity * b.unit_cost) FILTER (WHERE b.expiry_date >= CURRENT_DATE) AS value,
               SUM(b.quantity) FILTER (WHERE b.unit_cost IS NULL AND b.expiry_date >= CURRENT_DATE) AS units_without_cost
        FROM batches b WHERE b.quantity > 0 GROUP BY b.medicine_id
        """).fetchall()}
    last = {r["medicine_id"]: r["last"] for r in conn.execute(
        f"""
        SELECT b.medicine_id, MAX(sm.movement_date) AS last FROM stock_movements sm JOIN batches b ON b.id = sm.batch_id
        WHERE sm.movement_type = 'DISPENSED' GROUP BY b.medicine_id
        """).fetchall()}
    cutoff = date.today() - timedelta(days=dead_days)
    dead = []
    for medicine in inventory.medicine_stock(conn):
        v = values.get(medicine["medicine_id"])
        if not v or not v["usable"]:
            continue
        last_sale = last.get(medicine["medicine_id"])
        if last_sale is None or last_sale.date() < cutoff:
            dead.append({"medicine_id": medicine["medicine_id"], "medicine": medicine["medicine"],
                         "strength": medicine["strength"], "usable_stock": v["usable"],
                         "stock_value": round(float(v["value"]), 2) if v["value"] is not None else None,
                         "units_without_cost": v["units_without_cost"] or 0,
                         "last_dispensed": last_sale,
                         "days_since_dispensed": (date.today() - last_sale.date()).days if last_sale else None})
    dead.sort(key=lambda r: -(r["stock_value"] or 0))
    slow = [r for r in rows if r["movement_class"] == "SLOW-MOVING" and r["usable_stock"] > 0]
    return {"fast": fast, "slow": slow, "dead": dead, "units_dispensed_90d": total,
            "method": "Fast = top medicines making up 80 % of units dispensed in 90 days; "
                      f"dead = usable stock not dispensed for {dead_days} days."}


def stockout_risk(conn: psycopg.Connection, days: int = 7) -> list[dict]:
    """Medicines expected to run out within `days` at the current rate of
    use, counting what is already on order only if it is due in time."""
    rows = analytics.reorder_recommendations(conn)
    due = {r["medicine_id"]: r["units"] for r in conn.execute(
        """
        SELECT poi.medicine_id, SUM(poi.quantity_ordered - poi.quantity_received) AS units
        FROM purchase_order_items poi JOIN purchase_orders po ON po.id = poi.purchase_order_id
        WHERE po.status IN ('APPROVED', 'ORDERED', 'PARTIALLY_RECEIVED')
          AND po.expected_delivery_date IS NOT NULL AND po.expected_delivery_date <= CURRENT_DATE + %s::int
        GROUP BY poi.medicine_id
        """, (days,)).fetchall()}
    risky = []
    for r in rows:
        if r["days_of_stock"] is None or r["average_daily_consumption"] <= 0:
            continue
        cover = r["days_of_stock"] + (due.get(r["medicine_id"], 0) / r["average_daily_consumption"])
        if cover <= days:
            risky.append({**r, "arriving_in_time": due.get(r["medicine_id"], 0),
                          "days_of_cover_including_arrivals": round(cover, 1)})
    risky.sort(key=lambda r: r["days_of_cover_including_arrivals"])
    return risky


# ------------------------------------------------------------
# What needs attention
# ------------------------------------------------------------

def attention(conn: psycopg.Connection, user: CurrentUser) -> list[dict]:
    """Actionable items for this user, most urgent first. Each item has a
    link to the page where it is resolved."""
    items: list[dict] = []

    def add(key, severity, title, detail, count, link, permission=None):
        if count and (permission is None or user.can(permission)):
            items.append({"key": key, "severity": severity, "title": title, "detail": detail, "count": count,
                          "link": link})

    one = lambda sql, *p: conn.execute(sql, p).fetchone()  # noqa: E731
    expired = one("SELECT COUNT(*) AS n, COALESCE(SUM(quantity), 0) AS units FROM batches "
                  "WHERE quantity > 0 AND expiry_date < CURRENT_DATE")
    add("expired", "CRITICAL", "Expired stock on the shelf",
        f"{expired['n']} batch(es), {expired['units']} units: quarantine and write off", expired["n"],
        "#/inventory?status=EXPIRED")
    critical = one("SELECT COUNT(*) AS n FROM batches WHERE quantity > 0 AND expiry_date >= CURRENT_DATE "
                   "AND expiry_date <= CURRENT_DATE + (SELECT (value #>> '{}')::int FROM app_settings "
                   "WHERE key = 'expiry.critical_days')")
    add("critical_expiry", "WARNING", "Batches close to expiry", f"{critical['n']} batch(es) in the critical window",
        critical["n"], "#/inventory?status=CRITICAL")

    stock_rows = inventory.medicine_stock(conn)
    out = [s for s in stock_rows if s["is_active"] and s["stock_status"] == inventory.OUT_OF_STOCK]
    add("out_of_stock", "CRITICAL", "Out of stock", ", ".join(s["medicine"] for s in out[:4])
        + (" …" if len(out) > 4 else ""), len(out), "#/reorder")
    reorder = analytics.reorder_recommendations(conn, only_needed=True)
    add("reorder", "WARNING", "Reorder needed", f"{len(reorder)} medicine(s) at or below the reorder point",
        len(reorder), "#/reorder", "purchasing.write")
    risk = stockout_risk(conn, 7)
    add("stockout_risk", "WARNING", "May run out this week",
        ", ".join(r["medicine"] for r in risk[:4]) + (" …" if len(risk) > 4 else ""), len(risk), "#/reorder")

    pending_adj = one("SELECT COUNT(*) AS n FROM stock_adjustments WHERE status = 'PENDING_APPROVAL' "
                      "AND requested_by <> %s", user.id)
    add("adjustments", "WARNING", "Adjustments awaiting your approval", "Above the approval threshold",
        pending_adj["n"], "#/adjustments?status=PENDING_APPROVAL", "stock.approve")
    counts = one("SELECT COUNT(*) AS n FROM stock_counts WHERE status = 'SUBMITTED'")
    add("counts", "INFO", "Stock counts to review and post", "Submitted counts", counts["n"], "#/stock-counts",
        "stock.approve")
    po = one("SELECT COUNT(*) FILTER (WHERE status = 'SUBMITTED' AND submitted_by <> %s) AS approve, "
             "COUNT(*) FILTER (WHERE status = 'APPROVED') AS to_order, "
             "COUNT(*) FILTER (WHERE status IN ('APPROVED', 'ORDERED', 'PARTIALLY_RECEIVED') "
             "AND expected_delivery_date < CURRENT_DATE) AS overdue, "
             "COUNT(*) FILTER (WHERE status IN ('ORDERED', 'PARTIALLY_RECEIVED') "
             "AND expected_delivery_date = CURRENT_DATE) AS due_today FROM purchase_orders", user.id)
    add("po_approve", "WARNING", "Purchase orders awaiting approval", "Submitted for approval", po["approve"],
        "#/purchasing?status=SUBMITTED", "purchasing.approve")
    add("po_order", "INFO", "Approved orders to send", "Mark them ordered once sent to the supplier", po["to_order"],
        "#/purchasing?status=APPROVED", "purchasing.write")
    add("po_overdue", "WARNING", "Overdue deliveries", "Past the expected delivery date", po["overdue"],
        "#/purchasing?overdue=true", "purchasing.receive")
    add("po_due", "INFO", "Deliveries expected today", "Receive them when they arrive", po["due_today"],
        "#/purchasing", "purchasing.receive")
    transfers = one("SELECT COUNT(*) FILTER (WHERE status = 'REQUESTED') AS approve, "
                    "COUNT(*) FILTER (WHERE status = 'APPROVED') AS dispatch, "
                    "COUNT(*) FILTER (WHERE status = 'DISPATCHED') AS receive FROM transfers")
    if "multi_location" in user.features:
        add("transfer_approve", "INFO", "Transfers awaiting approval", "Requests and requisitions",
            transfers["approve"], "#/transfers", "transfers.approve")
        add("transfer_dispatch", "INFO", "Transfers to dispatch", "Approved, not yet sent", transfers["dispatch"],
            "#/transfers", "transfers.dispatch")
        add("transfer_receive", "INFO", "Transfers in transit", "Confirm receipt at the destination",
            transfers["receive"], "#/transfers", "transfers.receive")
    mismatches = stock.reconciliation(conn) if user.can("stock.adjust") else []
    add("reconciliation", "CRITICAL", "Stock ledger mismatches", "Batch quantity differs from its movements",
        len(mismatches), "#/reconciliation", "stock.adjust")
    failed = one("SELECT COUNT(*) AS n FROM notification_deliveries WHERE status = 'FAILED' "
                 "AND created_at >= CURRENT_DATE - 7")
    add("failed_messages", "WARNING", "Messages that could not be delivered", "Last 7 days", failed["n"],
        "#/delivery", "settings.manage")

    org = one("SELECT subscription_status, onboarding_completed_at, status, trial_ends_at FROM organizations "
              "WHERE id = %s", user.organization_id)
    if org["subscription_status"] in ("PAST_DUE", "TRIAL_ENDED"):
        add("subscription", "CRITICAL", "Subscription needs attention",
            "The trial has ended" if org["subscription_status"] == "TRIAL_ENDED" else "The paid period has ended",
            1, "#/billing", "billing.manage")
    if org["onboarding_completed_at"] is None:
        add("onboarding", "INFO", "Finish setting up", "Complete the set-up steps", 1, "#/onboarding",
            "settings.manage")
    if user.role in ("OWNER", "ADMINISTRATOR") and not user.mfa_enabled:
        add("mfa", "WARNING", "Turn on two-step verification", "Protect your administrator account", 1, "#/account")

    order = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    items.sort(key=lambda i: order[i["severity"]])
    return items


# ------------------------------------------------------------
# Daily brief
# ------------------------------------------------------------

def daily_brief(conn: psycopg.Connection, user: CurrentUser, day: date | None = None) -> dict:
    """A short, factual summary of yesterday and today, with the reasons."""
    today = day or date.today()
    yesterday = today - timedelta(days=1)
    sales = {r["day"]: r for r in conn.execute(
        """
        SELECT dispensed_at::date AS day, COUNT(*) AS transactions, COALESCE(SUM(total_amount), 0) AS sales
        FROM dispensations WHERE status = 'COMPLETED' AND dispensed_at >= %s AND dispensed_at < %s + 1
        GROUP BY dispensed_at::date
        """, (yesterday - timedelta(days=7), today)).fetchall()}

    def day_figures(d):
        row = sales.get(d)
        return {"date": d, "transactions": row["transactions"] if row else 0,
                "sales": round(float(row["sales"]), 2) if row else 0.0}

    previous_week_day = yesterday - timedelta(days=7)
    expiring = conn.execute(
        """
        SELECT m.name AS medicine, b.batch_number, b.expiry_date, b.quantity FROM batches b
        JOIN medicines m ON m.id = b.medicine_id
        WHERE b.quantity > 0 AND b.expiry_date >= %s AND b.expiry_date < %s + 7 ORDER BY b.expiry_date LIMIT 10
        """, (today, today)).fetchall()
    deliveries = conn.execute(
        """
        SELECT po.id, po.order_number, s.name AS supplier, po.expected_delivery_date
        FROM purchase_orders po JOIN suppliers s ON s.id = po.supplier_id
        WHERE po.status IN ('APPROVED', 'ORDERED', 'PARTIALLY_RECEIVED') AND po.expected_delivery_date <= %s
        ORDER BY po.expected_delivery_date LIMIT 10
        """, (today,)).fetchall()
    adjustments = conn.execute(
        """
        SELECT COUNT(*) AS n, COALESCE(SUM(abs(adjustment_quantity)), 0) AS units,
               COUNT(*) FILTER (WHERE reason_code IN ('THEFT_LOSS', 'OTHER')) AS unexplained
        FROM stock_adjustments WHERE status = 'POSTED' AND requested_at >= %s AND requested_at < %s
        """, (yesterday, today)).fetchone()
    risk = stockout_risk(conn, 7)
    y, w = day_figures(yesterday), day_figures(previous_week_day)
    lines = [f"Yesterday: {y['transactions']} sale(s)"
             + (f", {y['sales']:,.2f} in value" if y["transactions"] else "")
             + (f" (same day last week: {w['transactions']})" if w["transactions"] or y["transactions"] else "") + "."]
    if risk:
        lines.append(f"{len(risk)} medicine(s) may run out within 7 days: "
                     + ", ".join(r["medicine"] for r in risk[:5]) + ".")
    if expiring:
        lines.append(f"{len(expiring)} batch(es) expire within 7 days.")
    overdue = [d for d in deliveries if d["expected_delivery_date"] < today]
    if deliveries:
        lines.append(f"{len(deliveries) - len(overdue)} delivery(ies) due today, {len(overdue)} overdue.")
    if adjustments["n"]:
        lines.append(f"{adjustments['n']} stock adjustment(s) yesterday ({adjustments['units']} units).")
    if len(lines) == 1 and not y["transactions"]:
        lines.append("Nothing else needs attention from yesterday's records.")
    return {"date": today, "yesterday": y, "same_day_last_week": w, "today_so_far": day_figures(today),
            "stockout_risk": risk[:10], "expiring_this_week": expiring, "deliveries": deliveries,
            "adjustments_yesterday": adjustments, "summary": lines,
            "attention": attention(conn, user)}


# ------------------------------------------------------------
# Global search
# ------------------------------------------------------------

def search(conn: psycopg.Connection, user: CurrentUser, q: str, limit: int = 6) -> dict:
    q = q.strip()
    like = f"%{q}%"
    results: dict[str, list] = {}
    results["medicines"] = conn.execute(
        """
        SELECT id, name, strength, dosage_form, gtin, is_active FROM medicines
        WHERE name ILIKE %s OR generic_name ILIKE %s OR brand_name ILIKE %s OR gtin = %s
        ORDER BY (lower(name) = lower(%s)) DESC, name LIMIT %s
        """, (like, like, like, q, q, limit)).fetchall()
    results["batches"] = conn.execute(
        """
        SELECT b.id, b.batch_number, b.quantity, b.expiry_date, m.name AS medicine, l.name AS location
        FROM batches b JOIN medicines m ON m.id = b.medicine_id JOIN locations l ON l.id = b.location_id
        WHERE b.batch_number ILIKE %s OR b.barcode_data = %s ORDER BY b.expiry_date LIMIT %s
        """, (like, q, limit)).fetchall()
    results["suppliers"] = conn.execute(
        "SELECT id, name, contact_person, phone FROM suppliers WHERE name ILIKE %s OR contact_person ILIKE %s "
        "ORDER BY name LIMIT %s", (like, like, limit)).fetchall()
    results["purchase_orders"] = conn.execute(
        """
        SELECT po.id, po.order_number, po.status, s.name AS supplier FROM purchase_orders po
        JOIN suppliers s ON s.id = po.supplier_id WHERE po.order_number ILIKE %s ORDER BY po.id DESC LIMIT %s
        """, (like, limit)).fetchall()
    # Customer names and phone numbers are searchable only by dispensing staff.
    if user.can("stock.dispense") or user.can("dispensing.void"):
        results["dispensations"] = conn.execute(
            """
            SELECT id, dispensation_number, dispensed_at, total_amount, status, patient_name FROM dispensations
            WHERE dispensation_number ILIKE %s OR patient_name ILIKE %s OR patient_phone = %s
            ORDER BY id DESC LIMIT %s
            """, (like, like, q, limit)).fetchall()
    if "stock_count" in user.features:
        results["stock_counts"] = conn.execute(
            "SELECT id, count_number, name, status FROM stock_counts WHERE count_number ILIKE %s OR name ILIKE %s "
            "ORDER BY id DESC LIMIT %s", (like, like, limit)).fetchall()
    if "multi_location" in user.features:
        results["transfers"] = conn.execute(
            "SELECT id, transfer_number, status FROM transfers WHERE transfer_number ILIKE %s ORDER BY id DESC LIMIT %s",
            (like, limit)).fetchall()
    if user.can("users.manage"):
        results["users"] = conn.execute(
            "SELECT id, username, full_name, role FROM users WHERE organization_id = %s "
            "AND (username ILIKE %s OR full_name ILIKE %s) ORDER BY username LIMIT %s",
            (user.organization_id, like, like, limit)).fetchall()
    return {"query": q, "results": results, "total": sum(len(v) for v in results.values())}
