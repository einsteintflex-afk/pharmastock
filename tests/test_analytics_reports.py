# Consumption, reorder, expiry risk, valuation, forecast, dashboard, reports
# and exports.

import csv
import io
from datetime import date, timedelta

import pytest
from openpyxl import load_workbook

from backend.services import reports


def _day(offset):
    return (date.today() + timedelta(days=offset)).isoformat()


@pytest.fixture(scope="module")
def history(api):
    """Medicine with 60 days of dispensing history: 3 units/day for the last
    30 days, 1 unit/day for the 30 days before (inserted directly so dates
    lie in the past)."""
    from tests.conftest import org_connection

    medicine = api.post("/medicines", {"name": "Analytics Test", "strength": "5 mg", "dosage_form": "Tablet",
                                       "reorder_level": 20}).json()
    # 100 units expiring in 20 days (at 3/day ~ 63 will be used: ~37 at risk);
    # 200 units expiring in 400 days.
    soon = api.post("/batches", {"medicine_id": medicine["id"], "batch_number": "AN-SOON", "quantity": 100,
                                 "expiry_date": _day(20), "unit_cost": 2.0}).json()
    late = api.post("/batches", {"medicine_id": medicine["id"], "batch_number": "AN-LATE", "quantity": 200,
                                 "expiry_date": _day(400), "unit_cost": 2.5}).json()
    with org_connection(1) as conn:
        for days_ago in range(1, 61):
            units = 3 if days_ago <= 30 else 1
            conn.execute(
                "INSERT INTO stock_movements (batch_id, movement_type, quantity, movement_date, reason) "
                "VALUES (%s, 'DISPENSED', %s, CURRENT_TIMESTAMP - %s * INTERVAL '1 day', 'history')",
                (late["id"], units, days_ago - 0.5))
            # Keep the ledger balanced: history came out of stock received earlier.
        conn.execute("INSERT INTO stock_movements (batch_id, movement_type, quantity, movement_date, reason) "
                     "VALUES (%s, 'RECEIVED', 120, CURRENT_TIMESTAMP - INTERVAL '61 days', 'history')", (late["id"],))
        conn.commit()
    return {"id": medicine["id"], "soon": soon["id"], "late": late["id"]}


def test_consumption_and_trend(api, history):
    row = next(r for r in api.get("/analytics/consumption").json()["medicines"] if r["medicine_id"] == history["id"])
    assert row["units_dispensed_last_30_days"] == 90
    assert row["units_dispensed_previous_30_days"] == 30
    assert row["units_dispensed_last_90_days"] == 120
    assert row["average_daily_consumption"] == 3.0
    assert row["average_weekly_consumption"] == 21.0
    assert row["trend"] == "INCREASING"
    legacy = next(r for r in api.get("/consumption").json() if r["medicine_id"] == history["id"])
    assert legacy["average_daily_consumption"] == 3.0


def test_slow_moving(api, history):
    rows = {r["medicine"]: r for r in api.get("/slow-moving-products").json()}
    assert rows["Analytics Test"]["status"] == "ACTIVE"
    assert rows["Paracetamol"]["status"] == "NO MOVEMENT"


def test_expiry_risk_projection(api, history):
    body = api.get("/analytics/expiry-risk").json()
    soon = next(r for r in body["batches"] if r["batch_number"] == "AN-SOON")
    # 3/day for 21 days (usable through the expiry date) = 63 used, 37 left.
    assert soon["projected_units_at_risk"] == 37
    assert soon["value_at_risk"] == 74.0
    assert soon["risk_level"] == "HIGH"
    late = next(r for r in body["batches"] if r["batch_number"] == "AN-LATE")
    assert late["risk_level"] == "LOW"
    # A batch with no consumption at all is fully at risk.
    para = [r for r in body["batches"] if r["medicine"] == "Paracetamol" and r["risk_level"] != "EXPIRED"]
    assert all(r["projected_units_at_risk"] == r["quantity"] for r in para)


def test_reorder_recommendations(api, history):
    rows = {r["medicine"]: r for r in api.get("/analytics/reorder").json()}
    analytics_row = rows["Analytics Test"]
    assert analytics_row["usable_stock"] == 300
    assert analytics_row["days_of_stock"] == 100.0  # 300 units / 3 per day
    assert analytics_row["reorder_recommended"] is False
    ibuprofen = rows["Ibuprofen"]
    assert ibuprofen["stock_status"] == "OUT OF STOCK"
    assert ibuprofen["reorder_recommended"] is True
    assert ibuprofen["recommended_quantity"] == 60  # reorder level; no consumption history
    only = api.get("/analytics/reorder", params={"only_needed": True}).json()
    assert all(r["reorder_recommended"] for r in only)


def test_valuation(api, history, db):
    value = api.get("/analytics/valuation").json()
    expected = db.execute("SELECT ROUND(SUM(quantity * unit_cost), 2) AS v FROM batches WHERE quantity > 0 "
                          "AND unit_cost IS NOT NULL").fetchone()["v"]
    assert value["total"]["value"] == pytest.approx(float(expected))
    unknown = db.execute("SELECT SUM(quantity) AS q FROM batches WHERE unit_cost IS NULL").fetchone()["q"]
    assert value["total"]["units_without_cost"] == unknown
    assert value["by_location"][0]["location"] == "Main Pharmacy"


def test_forecast_and_turnover(api, history):
    forecast = next(r for r in api.get("/analytics/forecast").json() if r["medicine"] == "Analytics Test")
    assert forecast["forecast_demand"] > 0 and forecast["data_sufficiency"] == "ADEQUATE"
    # Demand tripled half-way through the history, so it is not regular enough for HIGH.
    assert forecast["confidence"] == "MEDIUM" and forecast["variability"] > 0.5
    assert api.get("/analytics/forecast", params={"horizon_days": 1}).status_code == 422
    turnover = next(r for r in api.get("/analytics/turnover").json() if r["medicine"] == "Analytics Test")
    assert turnover["dispensed_90d"] == 120 and turnover["turnover_90d"] > 0
    assert api.get("/analytics/purchasing").json()["by_supplier"][0]["supplier"] == "MedSupply Ghana Ltd"


def test_dashboard(api, history):
    data = api.get("/dashboard").json()
    for key in ("total_medicines", "total_units", "stock_value", "expired", "critical", "urgent",
                "low_stock_count", "out_of_stock_count", "slow_moving_count", "expiry_risk", "reorder",
                "recent_movements", "stock_levels"):
        assert key in data
    assert data["total_medicines"] == 4
    assert data["out_of_stock_count"] >= 1


@pytest.mark.parametrize("key", list(reports.REPORTS))
def test_every_report_every_format(api, history, key):
    body = api.get(f"/reports/{key}").json()
    assert body["title"] and isinstance(body["rows"], list) and body["columns"]

    csv_response = api.get(f"/reports/{key}", params={"format": "csv"})
    assert csv_response.status_code == 200
    assert "attachment" in csv_response.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(csv_response.content.decode("utf-8-sig"))))
    assert rows[0] == [c["label"] for c in body["columns"]]

    xlsx = api.get(f"/reports/{key}", params={"format": "xlsx"})
    assert xlsx.status_code == 200
    sheet = load_workbook(io.BytesIO(xlsx.content)).active
    assert sheet["A1"].value == body["title"]

    pdf = api.get(f"/reports/{key}", params={"format": "pdf"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")


def test_report_filters_and_errors(api):
    moves = api.get("/reports/stock-movements", params={"movement_type": "DISPENSED",
                                                        "date_from": _day(-10), "date_to": _day(0)}).json()
    assert all(r["movement_type"] == "DISPENSED" for r in moves["rows"])
    assert api.get("/reports/stock-movements", params={"date_from": _day(0), "date_to": _day(-5)}).status_code == 400
    assert api.get("/reports/nonexistent").status_code == 404
    assert api.get("/reports/inventory", params={"format": "exe"}).status_code == 422


def test_csv_formula_injection_neutralised(api):
    api.post("/medicines", {"name": "=HYPERLINK(\"http://x\")", "strength": "1", "dosage_form": "Tab"})
    content = api.get("/reports/low-stock", params={"format": "csv"}).content.decode("utf-8-sig")
    assert "'=HYPERLINK" in content
    assert "\n=HYPERLINK" not in content and ",=HYPERLINK" not in content


def test_export_is_audited(db):
    row = db.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'EXPORT'").fetchone()
    assert row["n"] >= 3 * len(reports.REPORTS)


def test_distant_unmoving_stock_is_medium_not_high(api):
    """A batch with no demand is at risk, but only HIGH when expiry is near."""
    medicine = api.post("/medicines", {"name": "Risk Horizon", "strength": "1 mg", "dosage_form": "Tablet"}).json()
    api.post("/batches", {"medicine_id": medicine["id"], "batch_number": "RH-FAR", "quantity": 10,
                          "expiry_date": _day(600), "unit_cost": 1})
    api.post("/batches", {"medicine_id": medicine["id"], "batch_number": "RH-NEAR", "quantity": 10,
                          "expiry_date": _day(150), "unit_cost": 1})
    rows = {r["batch_number"]: r for r in api.get("/analytics/expiry-risk").json()["batches"]}
    assert rows["RH-FAR"]["risk_level"] == "MEDIUM" and rows["RH-FAR"]["projected_units_at_risk"] == 10
    assert rows["RH-NEAR"]["risk_level"] == "HIGH"
