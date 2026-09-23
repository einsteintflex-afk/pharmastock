# Expiry engine, FEFO, stock movements and ledger reconciliation.

from datetime import date, timedelta

import pytest


def _day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


@pytest.fixture(scope="module")
def med(api):
    """A medicine with batches at every expiry status (dates relative to today)."""
    medicine = api.post("/medicines", {"name": "FEFO Test", "strength": "10 mg", "dosage_form": "Tablet",
                                       "reorder_level": 10}).json()
    batches = {}
    for number, offset, qty, cost in [
        ("F-EXPIRED", -3, 20, 1.0), ("F-TODAY", 0, 5, 1.0), ("F-CRIT", 10, 30, 1.0),
        ("F-URG", 60, 40, 2.0), ("F-APPR", 150, 50, 2.0), ("F-NORM", 400, 60, None),
    ]:
        response = api.post("/batches", {"medicine_id": medicine["id"], "batch_number": number, "quantity": qty,
                                         "expiry_date": _day(offset), "unit_cost": cost})
        assert response.status_code == 200, response.text
        batches[number] = response.json()["id"]
    return {"id": medicine["id"], "batches": batches}


def test_expiry_statuses(api, med):
    rows = {r["batch_number"]: r for r in api.get("/inventory", params={"medicine_id": med["id"]}).json()}
    assert rows["F-EXPIRED"]["status"] == "EXPIRED"
    assert rows["F-TODAY"]["status"] == "CRITICAL"  # usable on its expiry date
    assert rows["F-CRIT"]["status"] == "CRITICAL"
    assert rows["F-URG"]["status"] == "URGENT"
    assert rows["F-APPR"]["status"] == "APPROACHING EXPIRY"
    assert rows["F-NORM"]["status"] == "NORMAL"
    assert rows["F-CRIT"]["days_until_expiry"] == 10


def test_thresholds_are_configurable(api, med):
    assert api.put("/settings", {"expiry.critical_days": 5}).status_code == 200
    rows = {r["batch_number"]: r for r in api.get("/inventory", params={"medicine_id": med["id"]}).json()}
    assert rows["F-CRIT"]["status"] == "URGENT"
    bad = api.put("/settings", {"expiry.critical_days": 100})  # would exceed urgent (90)
    assert bad.status_code == 400
    assert api.put("/settings", {"expiry.critical_days": 30}).status_code == 200


def test_inventory_filters(api, med):
    expired = api.get("/inventory", params={"status": "EXPIRED", "medicine_id": med["id"]}).json()
    assert [r["batch_number"] for r in expired] == ["F-EXPIRED"]
    found = api.get("/inventory", params={"search": "F-URG"}).json()
    assert [r["batch_number"] for r in found] == ["F-URG"]
    assert api.get("/inventory", params={"status": "BOGUS"}).status_code == 422


def test_usable_stock_excludes_expired(api, med):
    row = next(r for r in api.get("/stock-alerts").json() if r["medicine_id"] == med["id"])
    assert row["current_stock"] == 5 + 30 + 40 + 50 + 60
    assert row["expired_stock"] == 20


def test_fefo_preview_orders_by_expiry_and_skips_expired(api, med):
    plan = api.get(f"/fefo/{med['id']}", params={"quantity": 50}).json()
    assert [a["batch_number"] for a in plan["allocations"]] == ["F-TODAY", "F-CRIT", "F-URG"]
    assert [a["allocate"] for a in plan["allocations"]] == [5, 30, 15]
    assert plan["shortfall"] == 0
    assert plan["available_usable_stock"] == 185


def test_dispense_fefo_splits_batches(api, med, db):
    result = api.post("/dispense", {"medicine_id": med["id"], "quantity": 50, "reason": "Rx 1001"},
                      role="PHARMACY_TECHNICIAN")
    assert result.status_code == 200, result.text
    moves = result.json()["movements"]
    assert [(m["batch_number"], m["quantity"]) for m in moves] == [("F-TODAY", 5), ("F-CRIT", 30), ("F-URG", 15)]
    quantities = {r["batch_number"]: r["quantity"] for r in db.execute(
        "SELECT batch_number, quantity FROM batches WHERE medicine_id = %s", (med["id"],))}
    assert quantities == {"F-EXPIRED": 20, "F-TODAY": 0, "F-CRIT": 0, "F-URG": 25, "F-APPR": 50, "F-NORM": 60}
    assert db.execute("SELECT user_id FROM stock_movements WHERE id = %s", (moves[0]["movement_id"],)
                      ).fetchone()["user_id"] is not None


def test_dispense_insufficient_changes_nothing(api, med, db):
    before = db.execute("SELECT SUM(quantity) AS q FROM batches WHERE medicine_id = %s", (med["id"],)).fetchone()["q"]
    response = api.post("/dispense", {"medicine_id": med["id"], "quantity": 10_000})
    assert response.status_code == 400
    assert response.json()["detail"]["available_usable_stock"] == 135
    after = db.execute("SELECT SUM(quantity) AS q FROM batches WHERE medicine_id = %s", (med["id"],)).fetchone()["q"]
    assert before == after


def test_fefo_enforced_on_single_batch_dispensing(api, med):
    blocked = api.post("/stock-movements", {"batch_id": med["batches"]["F-NORM"], "movement_type": "DISPENSED",
                                            "quantity": 1})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["fefo_batch_number"] == "F-URG"

    technician = api.post("/stock-movements", {"batch_id": med["batches"]["F-NORM"], "movement_type": "DISPENSED",
                                               "quantity": 1, "fefo_override_reason": "patient request"},
                          role="PHARMACY_TECHNICIAN")
    assert technician.status_code == 403

    override = api.post("/stock-movements", {"batch_id": med["batches"]["F-NORM"], "movement_type": "DISPENSED",
                                             "quantity": 1, "fefo_override_reason": "Earlier batch damaged packaging"},
                        role="PHARMACIST")
    assert override.status_code == 200
    assert "FEFO override" in override.json()["reason"]

    fefo_batch = api.post("/stock-movements", {"batch_id": med["batches"]["F-URG"], "movement_type": "DISPENSED",
                                               "quantity": 1})
    assert fefo_batch.status_code == 200


def test_expired_batch_cannot_be_dispensed(api, med):
    response = api.post("/stock-movements", {"batch_id": med["batches"]["F-EXPIRED"], "movement_type": "DISPENSED",
                                             "quantity": 1})
    assert response.status_code == 400


def test_expired_write_off_only_for_expired(api, med):
    assert api.post("/stock-movements", {"batch_id": med["batches"]["F-APPR"], "movement_type": "EXPIRED",
                                         "quantity": 1}).status_code == 400
    ok = api.post("/stock-movements", {"batch_id": med["batches"]["F-EXPIRED"], "movement_type": "EXPIRED",
                                       "quantity": 20, "reason": "Disposal"})
    assert ok.status_code == 200 and ok.json()["new_batch_quantity"] == 0


def test_adjustment_records_signed_change(api, med, db):
    # Original contract: quantity is the counted quantity.
    response = api.post("/stock-movements", {"batch_id": med["batches"]["F-APPR"], "movement_type": "ADJUSTMENT",
                                             "quantity": 47, "reason": "Stock count"})
    assert response.status_code == 200
    body = response.json()
    assert body["new_batch_quantity"] == 47 and body["quantity"] == -3
    assert api.post("/stock-movements", {"batch_id": med["batches"]["F-APPR"], "movement_type": "ADJUSTMENT",
                                         "quantity": 47, "reason": "recount"}).status_code == 400  # no change
    assert api.post("/stock-movements", {"batch_id": med["batches"]["F-APPR"], "movement_type": "ADJUSTMENT",
                                         "quantity": 40}).status_code == 400  # reason required
    # Stock found in an emptied batch.
    zero = api.post("/stock-movements", {"batch_id": med["batches"]["F-TODAY"], "movement_type": "ADJUSTMENT",
                                         "quantity": 3, "reason": "found stock"})
    assert zero.status_code == 200


def test_returned_and_received(api, med):
    returned = api.post("/stock-movements", {"batch_id": med["batches"]["F-URG"], "movement_type": "RETURNED",
                                             "quantity": 2, "reason": "Ward return"})
    assert returned.status_code == 200
    received = api.post("/stock-movements", {"batch_id": med["batches"]["F-NORM"], "movement_type": "RECEIVED",
                                             "quantity": 10, "reason": "Donation"}, role="STOREKEEPER")
    assert received.status_code == 200


def test_invalid_movements(api, med):
    assert api.post("/stock-movements", {"batch_id": med["batches"]["F-URG"], "movement_type": "STOLEN",
                                         "quantity": 1}).status_code == 400
    assert api.post("/stock-movements", {"batch_id": 999999, "movement_type": "RETURNED",
                                         "quantity": 1}).status_code == 404
    assert api.post("/stock-movements", {"batch_id": med["batches"]["F-URG"], "movement_type": "DAMAGED",
                                         "quantity": 10_000, "reason": "x"}).status_code == 400


def test_ledger_still_reconciled(api):
    assert api.get("/stock-reconciliation").json() == {"reconciled": True, "mismatches": []}


def test_batch_detail_history_balances(api, med):
    detail = api.get(f"/batches/{med['batches']['F-URG']}").json()
    assert detail["reconciled"] is True
    assert detail["history"][-1]["balance_after"] == detail["batch"]["quantity"]


def test_batch_update_does_not_touch_quantity(api, med, db):
    batch_id = med["batches"]["F-NORM"]
    before = db.execute("SELECT quantity FROM batches WHERE id = %s", (batch_id,)).fetchone()["quantity"]
    response = api.put(f"/batches/{batch_id}", {"batch_number": "F-NORM", "expiry_date": _day(400),
                                                "unit_cost": 3.25, "reason": "Invoice cost entered"})
    assert response.status_code == 200
    row = db.execute("SELECT quantity, unit_cost FROM batches WHERE id = %s", (batch_id,)).fetchone()
    assert row["quantity"] == before and float(row["unit_cost"]) == 3.25
    audit = db.execute("SELECT old_value, new_value FROM audit_log WHERE entity_type = 'batch' AND entity_id = %s "
                       "AND action = 'UPDATE' ORDER BY id DESC LIMIT 1", (str(batch_id),)).fetchone()
    assert audit["old_value"]["unit_cost"] is None and audit["new_value"]["unit_cost"] == 3.25


def test_medicine_detail(api, med):
    detail = api.get(f"/medicines/{med['id']}").json()
    assert detail["medicine"]["medicine"] == "FEFO Test"
    assert [b["batch_number"] for b in detail["fefo_order"]][0] == "F-TODAY"
    assert detail["movements"] and detail["batches"]
    assert api.get("/medicines/999999").status_code == 404
