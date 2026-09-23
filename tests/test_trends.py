# Trends, stock-outs, locations, suppliers, forecast sufficiency, reorder
# explanations, dashboard additions, new reports and assistant tools.

from datetime import date, timedelta

import pytest

from tests.conftest import org_connection


def _day(offset):
    return (date.today() + timedelta(days=offset)).isoformat()


@pytest.fixture(scope="module")
def data(api):
    """'Trend Med': 50 units received 41 days ago, 5/day dispensed on days
    -40 .. -31 (out of stock from day -31), 30 more received on day -20,
    nothing dispensed since. Whole-day offsets keep calendar dates exact."""
    medicine = api.post("/medicines", {"name": "Trend Med", "strength": "1 mg", "reorder_level": 5}).json()
    batch = api.post("/batches", {"medicine_id": medicine["id"], "batch_number": "TR-1", "quantity": 0,
                                  "expiry_date": _day(300), "unit_cost": 1.0}).json()
    with org_connection(1) as conn:
        def move(kind, qty, days_ago):
            conn.execute("INSERT INTO stock_movements (batch_id, movement_type, quantity, movement_date, reason) "
                         "VALUES (%s, %s, %s, CURRENT_TIMESTAMP - %s * INTERVAL '1 day', 'history')",
                         (batch["id"], kind, qty, days_ago))
        move("RECEIVED", 50, 41)
        for d in range(10):
            move("DISPENSED", 5, 40 - d)
        move("RECEIVED", 30, 20)
        conn.execute("UPDATE batches SET quantity = 30 WHERE id = %s", (batch["id"],))
        conn.commit()
    return {"medicine": medicine["id"], "batch": batch["id"]}


def test_ledger_is_consistent(api, data):
    assert api.get("/stock-reconciliation").json()["reconciled"] is True


def test_stockout_history(api, data):
    result = api.get("/analytics/stockouts", params={"days": 60}).json()
    row = next(r for r in result["medicines"] if r["medicine_id"] == data["medicine"])
    # Closing stock is zero from day -31 (last dispense) through day -21;
    # the receipt on day -20 ends it: 11 days.
    assert row["stockout_days"] == 11
    assert row["stockout_episodes"] == 1
    assert row["currently_out"] is False
    assert row["days_observed"] == 42  # from the first movement on day -41
    assert result["limitations"]


def test_stock_trends_closing_balance(api, data):
    result = api.get("/analytics/stock-trends", params={"months": 3}).json()
    months = result["months"]
    assert len(months) == 3
    assert months[-1]["closing_units"] == result["current_units"]
    total_received = sum(m["received"] for m in months)
    total_dispensed = sum(m["dispensed"] for m in months)
    assert total_received >= 80 and total_dispensed >= 50
    # Opening + net changes = closing, month by month.
    for previous, month in zip(months, months[1:]):
        assert previous["closing_units"] + month["net_change"] == month["closing_units"]


def test_expiry_trends(api, data):
    result = api.get("/analytics/expiry-trends", params={"months": 12}).json()
    assert len(result["expiring_by_month"]) == 12
    assert sum(m["units"] for m in result["expiring_by_month"]) >= 30  # TR-1 expires in ~300 days
    assert "expired_on_shelf" in result


def test_location_and_supplier_analytics(api, data):
    locations = api.get("/analytics/locations").json()
    main = next(r for r in locations if r["location"] == "Main Pharmacy")
    assert main["units"] >= 30 and "open_transfers" in main
    suppliers = api.get("/analytics/suppliers").json()
    assert "suppliers" in suppliers and suppliers["method"]
    assert len(api.get("/analytics/purchasing-trends", params={"months": 6}).json()) == 6


def test_forecast_reports_data_sufficiency(api, data):
    rows = {r["medicine_id"]: r for r in api.get("/analytics/forecast").json()}
    trend = rows[data["medicine"]]
    # 6 weeks tracked, but dispensing in only 2 of them: not enough to forecast from.
    assert trend["weeks_of_history"] == 6 and trend["active_weeks"] == 2
    assert trend["data_sufficiency"] == "INSUFFICIENT" and trend["confidence"] == "LOW"
    assert trend["limitations"] and trend["notes"]
    # A medicine never dispensed is flagged, not predicted.
    never = next(r for r in rows.values() if r["active_weeks"] == 0)
    assert never["data_sufficiency"] == "INSUFFICIENT" and never["confidence"] == "LOW"
    assert never["projected_stockout_date"] is None


def test_reorder_explains_calculation(api, data):
    rows = api.get("/analytics/reorder").json()
    assert all("calculation" in r for r in rows)
    with_rate = [r for r in rows if r["average_daily_consumption"]]
    assert all(r["projected_stockout_date"] for r in with_rate)


def test_dashboard_additions(api, data):
    body = api.get("/dashboard").json()
    for key in ("recent_purchases", "supplier_activity", "open_transfers", "held_batches"):
        assert key in body
    assert set(body["open_transfers"]) == {"awaiting_approval", "awaiting_dispatch", "in_transit"}


def test_audit_report_needs_permission(api):
    assert api.get("/reports/audit", role="PHARMACIST").status_code == 403
    assert "audit" not in [r["key"] for r in api.get("/reports", role="PHARMACIST").json()]
    assert api.get("/reports/audit", role="MANAGER").status_code == 200


def test_report_views_are_audited(api, db):
    api.get("/reports/reorder")
    row = db.execute("SELECT action, new_value FROM audit_log WHERE entity_type = 'report' "
                     "AND entity_id = 'reorder' ORDER BY id DESC LIMIT 1").fetchone()
    assert row["action"] == "VIEW_REPORT"


def test_assistant_tools(api, data):
    from backend.services import assistant
    with org_connection(1) as raw:
        from psycopg.rows import dict_row
        raw.row_factory = dict_row
        summary = assistant.run_tool(raw, "monthly_summary", {})
        assert summary["month"] == date.today().strftime("%Y-%m")
        changes = assistant.run_tool(raw, "stock_changes", {"date_from": _day(-45), "date_to": _day(0)})
        trend = next(r for r in changes["rows"] if r["medicine"] == "Trend Med")
        assert (trend["received"], trend["dispensed"], trend["net_change"]) == (80, 50, 30)
        # 30 units at 50/90 per day = 54 days of stock: overstocked only against a 30-day threshold.
        assert not any(r["medicine"] == "Trend Med" for r in assistant.run_tool(raw, "overstock", {})["rows"])
        overstock = assistant.run_tool(raw, "overstock", {"min_days_of_stock": 30})
        assert any(r["medicine"] == "Trend Med" and r["days_of_stock"] == 54.0 for r in overstock["rows"])
        assert "error" in assistant.run_tool(raw, "stock_changes", {"date_from": _day(0), "date_to": _day(-3)})
        assert "error" in assistant.run_tool(raw, "monthly_summary", {"month": "2026-13"})
