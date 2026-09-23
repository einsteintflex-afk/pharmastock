# Medicine master data, GS1 barcodes, batch holds, reconciliation actions,
# pagination and supplier receipt history.

from datetime import date, timedelta

import pytest

from backend.services import barcode


def _day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


# ---------------------------------------------------------------- barcodes

def test_gtin_check_digit():
    assert barcode.valid_gtin("4006381333931")          # EAN-13
    assert barcode.valid_gtin("96385074")                # EAN-8
    assert not barcode.valid_gtin("4006381333932")
    assert barcode.normalize_gtin("4006381333931") == "04006381333931"
    with pytest.raises(barcode.BarcodeError):
        barcode.normalize_gtin("12345")


def test_parse_gs1_datamatrix_with_separators():
    raw = "0104006381333931" + "17271231" + "10LOT-A1\x1d" + "21SN998"
    result = barcode.parse(raw)
    assert result["gtin"] == "04006381333931"
    assert result["expiry_date"] == date(2027, 12, 31)
    assert result["batch_number"] == "LOT-A1"
    assert result["serial_number"] == "SN998"


def test_parse_variants():
    # Human-readable form, day 00 = end of month, symbology prefix, <GS> token.
    readable = barcode.parse("(01)04006381333931(17)280200(10)B77")
    assert readable["expiry_date"] == date(2028, 2, 29) and readable["batch_number"] == "B77"
    prefixed = barcode.parse("]d20104006381333931" + "10X1<GS>17290101")
    assert prefixed["batch_number"] == "X1" and prefixed["expiry_date"] == date(2029, 1, 1)
    # Plain product barcode; not every code has batch or expiry.
    plain = barcode.parse("4006381333931")
    assert plain == {"format": "GTIN", "gtin": "04006381333931", "raw": "4006381333931"}
    for bad in ["", "0104006381333932", "(17)270101", "99ABC", "0104006381333931" + "17271341"]:
        with pytest.raises(barcode.BarcodeError):
            barcode.parse(bad)


# ---------------------------------------------------------------- medicines

@pytest.fixture(scope="module")
def product(api):
    response = api.post("/medicines", {
        "name": "Amoxicillin", "strength": "250 mg", "dosage_form": "Capsule", "reorder_level": 5,
        "generic_name": "Amoxicillin", "brand_name": "Amoxil", "route": "Oral", "manufacturer": "GSK",
        "gtin": "4006381333931",
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_master_fields_saved(api, product):
    assert product["gtin"] == "04006381333931" and product["brand_name"] == "Amoxil" and product["is_active"]
    found = api.get("/medicines", params={"search": "Amoxil"}).json()
    assert [m["id"] for m in found] == [product["id"]]
    by_barcode = api.get("/medicines", params={"search": "4006381333931"}).json()
    assert [m["id"] for m in by_barcode] == [product["id"]]


def test_gtin_validation_and_uniqueness(api, product):
    bad = api.post("/medicines", {"name": "Bad", "gtin": "4006381333932"})
    assert bad.status_code == 422
    duplicate = api.post("/medicines", {"name": "Other", "gtin": "04006381333931"})
    assert duplicate.status_code == 409


def test_update_without_new_fields_keeps_them(api, product):
    # An original-API client sends only the original fields.
    response = api.put(f"/medicines/{product['id']}", {"name": "Amoxicillin", "strength": "250 mg",
                                                       "dosage_form": "Capsule", "reorder_level": 8})
    assert response.status_code == 200
    body = response.json()
    assert body["reorder_level"] == 8 and body["brand_name"] == "Amoxil" and body["gtin"] == "04006381333931"


def test_inactive_medicine_cannot_be_stocked_or_ordered(api):
    medicine = api.post("/medicines", {"name": "Discontinued Syrup", "is_active": False}).json()
    assert medicine["is_active"] is False
    batch = api.post("/batches", {"medicine_id": medicine["id"], "batch_number": "D1", "quantity": 5,
                                  "expiry_date": _day(300)})
    assert batch.status_code == 400
    assert medicine["id"] in [m["id"] for m in api.get("/medicines", params={"active": False}).json()]
    assert medicine["id"] not in [m["id"] for m in api.get("/medicines", params={"active": True}).json()]


# ---------------------------------------------------------------- batch holds

def test_quarantine_excludes_batch_from_fefo(api, product, db):
    early = api.post("/batches", {"medicine_id": product["id"], "batch_number": "Q-EARLY", "quantity": 10,
                                  "expiry_date": _day(40), "purchase_date": _day(-10)}).json()
    late = api.post("/batches", {"medicine_id": product["id"], "batch_number": "Q-LATE", "quantity": 10,
                                 "expiry_date": _day(300)}).json()
    assert early["purchase_date"] == _day(-10)

    # Viewers and technicians cannot quarantine.
    assert api.post(f"/batches/{early['id']}/status", {"batch_status": "QUARANTINED", "reason": "Damaged seal"},
                    role="VIEWER").status_code == 403
    held = api.post(f"/batches/{early['id']}/status", {"batch_status": "QUARANTINED", "reason": "Damaged seal"})
    assert held.status_code == 200 and held.json()["batch_status"] == "QUARANTINED"

    plan = api.get(f"/fefo/{product['id']}", params={"quantity": 5}).json()
    assert [a["batch_id"] for a in plan["allocations"]] == [late["id"]]
    stock = next(r for r in api.get("/stock-alerts").json() if r["medicine_id"] == product["id"])
    assert stock["current_stock"] == 10
    blocked = api.post("/stock-movements", {"batch_id": early["id"], "movement_type": "DISPENSED", "quantity": 1})
    assert blocked.status_code == 400 and "quarantined" in blocked.json()["detail"]
    detail = api.get(f"/medicines/{product['id']}").json()["medicine"]
    assert detail["held_stock"] == 10 and detail["usable_stock"] == 10

    # Recall: only a manager/administrator may release it.
    assert api.post(f"/batches/{early['id']}/status", {"batch_status": "RECALLED",
                                                       "reason": "Manufacturer recall"}).status_code == 200
    assert api.post(f"/batches/{early['id']}/status", {"batch_status": "ACTIVE", "reason": "Cleared"},
                    role="PHARMACIST").status_code == 403
    assert api.post(f"/batches/{early['id']}/status", {"batch_status": "ACTIVE", "reason": "Cleared"},
                    role="MANAGER").status_code == 200
    assert api.get("/inventory", params={"batch_status": "RECALLED"}).json() == []
    audit = db.execute("SELECT action FROM audit_log WHERE entity_type = 'batch' AND entity_id = %s "
                       "AND action LIKE 'BATCH_%%' ORDER BY id", (str(early["id"]),)).fetchall()
    assert [a["action"] for a in audit] == ["BATCH_QUARANTINED", "BATCH_RECALLED", "BATCH_ACTIVE"]


# ---------------------------------------------------------------- reconciliation

def test_reconciliation_actions(api, product, db):
    batch = api.post("/batches", {"medicine_id": product["id"], "batch_number": "R-1", "quantity": 20,
                                  "expiry_date": _day(200)}).json()
    other = api.post("/batches", {"medicine_id": product["id"], "batch_number": "R-2", "quantity": 20,
                                  "expiry_date": _day(210)}).json()
    # Simulate quantities changed outside the application.
    db.execute("UPDATE batches SET quantity = 25 WHERE id = %s", (batch["id"],))
    db.execute("UPDATE batches SET quantity = 17 WHERE id = %s", (other["id"],))
    db.commit()
    report = api.get("/stock-reconciliation").json()
    assert report["reconciled"] is False and len(report["mismatches"]) == 2

    body = {"action": "TRUST_LEDGER", "reason": "Direct database edit reverted"}
    assert api.post(f"/stock-reconciliation/{batch['id']}/resolve", body, role="VIEWER").status_code == 403
    ledger = api.post(f"/stock-reconciliation/{batch['id']}/resolve", body)
    assert ledger.status_code == 200 and ledger.json()["quantity"] == 20

    count = api.post(f"/stock-reconciliation/{other['id']}/resolve",
                     {"action": "TRUST_COUNT", "reason": "Physical count confirmed 17"})
    assert count.status_code == 200 and count.json()["adjustment_movement_id"]
    movement = db.execute("SELECT movement_type, quantity FROM stock_movements WHERE id = %s",
                          (count.json()["adjustment_movement_id"],)).fetchone()
    assert movement == {"movement_type": "ADJUSTMENT", "quantity": -3}

    assert api.get("/stock-reconciliation").json()["reconciled"] is True
    assert api.post(f"/stock-reconciliation/{batch['id']}/resolve", body).status_code == 400
    assert db.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'RECONCILE'").fetchone()["n"] == 2


# ---------------------------------------------------------------- pagination

def test_pagination_headers(api):
    everything = api.get("/medicines")
    total = int(everything.headers["x-total-count"])
    assert total == len(everything.json()) and total >= 5
    page = api.get("/medicines", params={"limit": 2, "offset": 1, "sort": "id"})
    assert page.headers["x-total-count"] == str(total)
    assert [m["id"] for m in page.json()] == [m["id"] for m in everything.json()][1:3]

    movements = api.get("/stock-movements", params={"limit": 3})
    assert len(movements.json()) == 3 and int(movements.headers["x-total-count"]) > 3
    second = api.get("/stock-movements", params={"limit": 3, "offset": 3}).json()
    assert not {m["id"] for m in second} & {m["id"] for m in movements.json()}

    audit = api.get("/audit-log", params={"limit": 2})
    assert len(audit.json()) == 2 and int(audit.headers["x-total-count"]) >= 2
    inventory = api.get("/inventory", params={"limit": 1})
    assert len(inventory.json()) == 1 and int(inventory.headers["x-total-count"]) > 1
    assert api.get("/medicines", params={"limit": 0}).status_code == 422


# ---------------------------------------------------------------- suppliers

def test_supplier_receipt_history(api, product):
    supplier = api.post("/suppliers", {"name": "Receipt History Ltd"}).json()
    number = api.get("/purchase-orders/next-number").json()["order_number"]
    order = api.post("/purchase-orders", {"supplier_id": supplier["id"], "order_number": number})
    assert order.status_code == 200, order.text
    order_id = order.json()["id"]
    item_id = api.post(f"/purchase-orders/{order_id}/items", {"medicine_id": product["id"],
                                                             "quantity_ordered": 30, "unit_cost": 1.5}).json()["id"]
    received = api.post("/purchase-receipts", {"purchase_order_item_id": item_id, "batch_number": "SUP-1",
                                               "quantity_received": 12, "expiry_date": _day(500),
                                               "barcode_data": "0104006381333931"})
    assert received.status_code == 200, received.text

    detail = api.get(f"/suppliers/{supplier['id']}").json()
    assert detail["summary"]["receipts"] == 1 and detail["summary"]["units_received"] == 12
    assert detail["receipts"][0]["batch_number"] == "SUP-1" and float(detail["receipts"][0]["value"]) == 18.0
    assert {"CREATE", "RECEIVE"} <= {a["action"] for a in detail["activity"]}
    batch = api.get(f"/batches/{received.json()['batch_id']}").json()["batch"]
    assert batch["purchase_date"] is not None and batch["barcode_data"] == "0104006381333931"


# ---------------------------------------------------------------- barcode lookup

def test_barcode_lookup(api, product):
    batch = api.post("/batches", {"medicine_id": product["id"], "batch_number": "SCAN-7", "quantity": 9,
                                  "expiry_date": _day(700)}).json()
    code = f"0104006381333931" + "17" + (date.today() + timedelta(days=700)).strftime("%y%m%d") + "10SCAN-7"
    result = api.post("/barcode/lookup", {"code": code}, role="PHARMACY_TECHNICIAN").json()
    assert result["medicine"]["id"] == product["id"]
    assert result["matched_batch"]["batch_id"] == batch["id"]
    # An earlier-expiring batch of this medicine exists, so FEFO warns.
    assert any("FEFO" in w for w in result["warnings"])
    assert result["fefo_batch"]["batch_id"] != batch["id"]

    wrong_expiry = api.post("/barcode/lookup", {"code": "(01)04006381333931(17)300101(10)SCAN-7"}).json()
    assert any("differs from the recorded expiry" in w for w in wrong_expiry["warnings"])
    unknown_lot = api.post("/barcode/lookup", {"code": "(01)04006381333931(10)NEW-LOT"}).json()
    assert unknown_lot["matched_batch"] is None and any("not on record" in w for w in unknown_lot["warnings"])
    plain = api.post("/barcode/lookup", {"code": "4006381333931"}).json()
    assert plain["medicine"]["id"] == product["id"] and plain["matched_batch"] is None
    unknown = api.post("/barcode/lookup", {"code": "96385074"}).json()
    assert unknown["medicine"] is None and unknown["warnings"]
    assert api.post("/barcode/lookup", {"code": "garbage!"}).status_code == 400
    parsed = api.post("/barcode/parse", {"code": "(01)04006381333931(21)ABC"}).json()
    assert parsed["serial_number"] == "ABC"
