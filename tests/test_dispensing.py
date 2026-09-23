# Dispensing counter: multi-line FEFO transactions, prices, prescriptions,
# all-or-nothing behaviour, voids, traceability, permissions, reports.

from datetime import date, timedelta

import pytest


def _day(offset):
    return (date.today() + timedelta(days=offset)).isoformat()


@pytest.fixture(scope="module")
def counter(api):
    """Two medicines: one priced with two batches, one unpriced."""
    priced = api.post("/medicines", {"name": "Counter Cough Syrup", "strength": "100 ml", "dosage_form": "Syrup",
                                     "reorder_level": 5, "selling_price": 12.50}).json()
    unpriced = api.post("/medicines", {"name": "Counter ORS", "strength": "sachet", "dosage_form": "Powder",
                                       "reorder_level": 5}).json()
    early = api.post("/batches", {"medicine_id": priced["id"], "batch_number": "CS-EARLY", "quantity": 3,
                                  "expiry_date": _day(40), "unit_cost": 6}).json()
    late = api.post("/batches", {"medicine_id": priced["id"], "batch_number": "CS-LATE", "quantity": 10,
                                 "expiry_date": _day(300), "unit_cost": 6}).json()
    ors = api.post("/batches", {"medicine_id": unpriced["id"], "batch_number": "ORS-1", "quantity": 50,
                                "expiry_date": _day(500)}).json()
    return {"priced": priced["id"], "unpriced": unpriced["id"],
            "early": early["id"], "late": late["id"], "ors": ors["id"]}


def test_selling_price_saved_and_kept_when_omitted(api, counter):
    med = next(m for m in api.get("/medicines").json() if m["id"] == counter["priced"])
    assert med["selling_price"] == 12.5
    # An update without selling_price (the original API contract) keeps the price.
    api.put(f"/medicines/{counter['priced']}", {"name": "Counter Cough Syrup", "strength": "100 ml",
                                                  "dosage_form": "Syrup", "reorder_level": 6})
    med = next(m for m in api.get("/medicines").json() if m["id"] == counter["priced"])
    assert med["selling_price"] == 12.5 and med["reorder_level"] == 6


def test_otc_multi_line_dispensation(api, counter, db):
    response = api.post("/dispensations", {
        "dispense_type": "OTC", "payment_method": "MOBILE_MONEY", "patient_name": "Walk-in",
        "items": [
            {"medicine_id": counter["priced"], "quantity": 5, "directions": "10 ml three times daily"},
            {"medicine_id": counter["unpriced"], "quantity": 2, "unit_price": 1.20},
        ],
    }, role="PHARMACY_TECHNICIAN")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["dispensation_number"].startswith(f"DSP-{date.today():%Y%m%d}-")
    assert body["status"] == "COMPLETED" and body["dispensed_by"] == "Test Pharmacy Technician"
    assert float(body["total_amount"]) == 5 * 12.50 + 2 * 1.20

    syrup = next(i for i in body["items"] if i["medicine_id"] == counter["priced"])
    # FEFO across batches: all 3 from the early batch, 2 from the later one.
    assert [(b["batch_number"], b["quantity"]) for b in syrup["batches"]] == [("CS-EARLY", 3), ("CS-LATE", 2)]
    assert syrup["directions"] == "10 ml three times daily"
    assert float(syrup["line_total"]) == 62.5

    quantities = {r["id"]: r["quantity"] for r in db.execute(
        "SELECT id, quantity FROM batches WHERE id = ANY(%s)", ([counter["early"], counter["late"], counter["ors"]],))}
    assert quantities == {counter["early"]: 0, counter["late"]: 8, counter["ors"]: 48}
    linked = db.execute("SELECT COUNT(*) AS n FROM stock_movements WHERE dispensation_item_id IS NOT NULL").fetchone()
    assert linked["n"] == 3
    assert api.get("/stock-reconciliation").json()["reconciled"]


def test_all_or_nothing(api, counter, db):
    before = db.execute("SELECT SUM(quantity) AS q FROM batches").fetchone()["q"]
    count = db.execute("SELECT COUNT(*) AS n FROM dispensations").fetchone()["n"]
    response = api.post("/dispensations", {
        "dispense_type": "OTC", "payment_method": "CASH",
        "items": [{"medicine_id": counter["unpriced"], "quantity": 1},
                  {"medicine_id": counter["priced"], "quantity": 999}],
    })
    assert response.status_code == 400
    assert response.json()["detail"]["available_usable_stock"] == 8
    assert db.execute("SELECT SUM(quantity) AS q FROM batches").fetchone()["q"] == before
    assert db.execute("SELECT COUNT(*) AS n FROM dispensations").fetchone()["n"] == count


def test_validation(api, counter):
    base = {"dispense_type": "OTC", "payment_method": "CASH"}
    assert api.post("/dispensations", {**base, "items": []}).status_code == 422
    dup = api.post("/dispensations", {**base, "items": [{"medicine_id": counter["unpriced"], "quantity": 1},
                                                        {"medicine_id": counter["unpriced"], "quantity": 2}]})
    assert dup.status_code == 400
    rx = api.post("/dispensations", {"dispense_type": "PRESCRIPTION", "payment_method": "NHIS",
                                     "items": [{"medicine_id": counter["unpriced"], "quantity": 1}]})
    assert rx.status_code == 400 and "prescri" in rx.json()["detail"]
    assert api.post("/dispensations", {**base, "payment_method": "BITCOIN",
                                       "items": [{"medicine_id": counter["unpriced"], "quantity": 1}]}).status_code == 422
    assert api.post("/dispensations", {**base, "items": [{"medicine_id": 999999, "quantity": 1}]}).status_code == 404
    assert api.post("/dispensations", {**base, "items": [{"medicine_id": counter["unpriced"], "quantity": 0}]}).status_code == 422


def test_expired_stock_never_dispensed(api):
    # PARA004 (expired) must not be used even though it holds stock.
    body = api.post("/dispensations", {"dispense_type": "PRESCRIPTION", "payment_method": "CASH",
                                       "prescriber": "Dr. Mensah", "prescription_number": "RX-77",
                                       "items": [{"medicine_id": 1, "quantity": 55}]}).json()
    batches = [b["batch_number"] for b in body["items"][0]["batches"]]
    assert "PARA004" not in batches and batches[0] == "PARA002"
    assert body["prescriber"] == "Dr. Mensah"


def test_permissions(api, counter):
    item = {"dispense_type": "OTC", "payment_method": "CASH", "items": [{"medicine_id": counter["unpriced"], "quantity": 1}]}
    assert api.post("/dispensations", item, role="VIEWER").status_code == 403
    assert api.post("/dispensations", item, role="STOREKEEPER").status_code == 403
    done = api.post("/dispensations", item, role="PHARMACY_TECHNICIAN").json()
    assert api.post(f"/dispensations/{done['id']}/void", {"reason": "wrong item"},
                    role="PHARMACY_TECHNICIAN").status_code == 403


def test_void_returns_stock_to_same_batches(api, counter, db):
    created = api.post("/dispensations", {"dispense_type": "OTC", "payment_method": "CASH",
                                          "items": [{"medicine_id": counter["priced"], "quantity": 4}]}).json()
    before = db.execute("SELECT quantity FROM batches WHERE id = %s", (counter["late"],)).fetchone()["quantity"]
    voided = api.post(f"/dispensations/{created['id']}/void", {"reason": "Customer changed mind"}, role="PHARMACIST")
    assert voided.status_code == 200
    body = voided.json()
    assert body["status"] == "VOIDED" and body["voided_by_name"] == "Test Pharmacist"
    after = db.execute("SELECT quantity FROM batches WHERE id = %s", (counter["late"],)).fetchone()["quantity"]
    assert after == before + 4
    returned = db.execute("SELECT movement_type, quantity FROM stock_movements WHERE movement_type = 'RETURNED' "
                          "AND reason LIKE %s", (f"Void of {created['dispensation_number']}%",)).fetchall()
    assert [(r["movement_type"], r["quantity"]) for r in returned] == [("RETURNED", 4)]
    assert api.post(f"/dispensations/{created['id']}/void", {"reason": "again"}).status_code == 400
    assert api.get("/stock-reconciliation").json()["reconciled"]


def test_history_search_summary_and_report(api):
    rows = api.get("/dispensations", params={"date_from": _day(0), "date_to": _day(0)}).json()
    assert len(rows) >= 4
    assert api.get("/dispensations", params={"search": "Walk-in"}).json()[0]["patient_name"] == "Walk-in"
    assert all(r["status"] == "VOIDED" for r in api.get("/dispensations", params={"status": "VOIDED"}).json())

    summary = api.get("/dispensations/summary").json()
    assert summary["voided"] == 1 and summary["dispensations"] >= 3
    methods = {m["payment_method"]: float(m["amount"]) for m in summary["by_payment_method"]}
    assert methods["MOBILE_MONEY"] == 64.9

    dashboard = api.get("/dashboard").json()["dispensing_today"]
    assert dashboard["dispensations"] == summary["dispensations"]

    report = api.get("/reports/dispensing").json()
    assert report["title"] == "Dispensing Report" and len(report["rows"]) == len(rows)
    assert api.get("/reports/dispensing", params={"format": "pdf"}).content.startswith(b"%PDF")


def test_dispensation_audited(db):
    actions = {r["action"] for r in db.execute("SELECT action FROM audit_log WHERE entity_type = 'dispensation'")}
    assert {"DISPENSE", "VOID"} <= actions


def test_settings_receipt_header(api):
    assert api.put("/settings", {"pharmacy.name": "Adom Community Pharmacy", "pharmacy.phone": "0302 000000"},
                   role="MANAGER").status_code == 200
    values = {s["key"]: s["value"] for s in api.get("/settings").json()}
    assert values["pharmacy.name"] == "Adom Community Pharmacy"
