# Regression: the original endpoints keep their paths, fields and behaviour.

from datetime import date


ORIGINAL_FIELDS = {
    "/medicines": {"id", "name", "strength", "dosage_form", "reorder_level"},
    "/inventory": {"medicine", "strength", "dosage_form", "batch_number", "quantity", "expiry_date",
                   "days_until_expiry", "status"},
    "/stock-alerts": {"medicine", "strength", "dosage_form", "current_stock", "reorder_level", "status"},
    "/stock-movements": {"id", "batch_id", "medicine", "strength", "dosage_form", "batch_number",
                         "movement_type", "quantity", "movement_date", "reason"},
    "/slow-moving-products": {"medicine_id", "medicine", "strength", "dosage_form",
                              "units_dispensed_last_90_days", "dispensing_transactions_last_90_days", "status"},
    "/consumption": {"medicine_id", "medicine", "strength", "dosage_form", "units_dispensed_last_30_days",
                     "average_daily_consumption", "average_weekly_consumption"},
    "/suppliers": {"id", "name", "contact_person", "phone", "email", "address", "is_active"},
    "/purchase-orders": {"id", "supplier_id", "supplier", "order_number", "order_date", "status", "notes",
                         "created_at"},
    "/purchase-orders/1/items": {"id", "purchase_order_id", "medicine_id", "medicine", "strength", "dosage_form",
                                 "quantity_ordered", "unit_cost", "quantity_received", "quantity_remaining"},
    "/purchase-receipts": {"id", "purchase_order_id", "order_number", "supplier", "medicine", "strength",
                           "dosage_form", "batch_number", "quantity_received", "received_date", "received_by", "notes"},
}


def test_original_fields_present(api):
    for path, fields in ORIGINAL_FIELDS.items():
        rows = api.get(path).json()
        assert rows, path
        missing = fields - set(rows[0])
        assert not missing, f"{path} missing {missing}"


def test_original_records_returned(api):
    medicines = api.get("/medicines").json()
    assert [(m["id"], m["name"]) for m in medicines] == [(1, "Paracetamol"), (2, "Amoxicillin"), (4, "Ibuprofen")]
    assert api.get("/suppliers").json()[0]["name"] == "MedSupply Ghana Ltd"
    order = api.get("/purchase-orders").json()[-1]
    assert (order["order_number"], order["status"]) == ("PO-2026-001", "RECEIVED")
    assert api.get("/purchase-orders/1/items").json()[0]["unit_cost"] == 5.5


def test_stock_alerts_include_medicine_id_and_usable_stock(api, db):
    """Fix: /stock-alerts had no medicine_id, so the Medicines page showed 0 for every medicine."""
    rows = {r["medicine"]: r for r in api.get("/stock-alerts").json()}
    for name, row in rows.items():
        expected = db.execute(
            """SELECT COALESCE(SUM(b.quantity) FILTER (WHERE b.expiry_date >= CURRENT_DATE), 0) AS usable,
                      COALESCE(SUM(b.quantity), 0) AS total
               FROM medicines m LEFT JOIN batches b ON b.medicine_id = m.id WHERE m.id = %s""",
            (row["medicine_id"],)).fetchone()
        assert row["current_stock"] == expected["usable"]
        assert row["total_stock"] == expected["total"]
    assert rows["Ibuprofen"]["status"] == "OUT OF STOCK"
    assert rows["Paracetamol"]["total_stock"] == 190
    assert rows["Amoxicillin"]["total_stock"] == 280


def test_inventory_status_and_days(api):
    for row in api.get("/inventory").json():
        days = (date.fromisoformat(row["expiry_date"]) - date.today()).days
        assert row["days_until_expiry"] == days
        if days < 0:
            assert row["status"] == "EXPIRED"


def test_create_and_update_medicine(api, db):
    created = api.post("/medicines", {"name": "Metformin", "strength": "500 mg", "dosage_form": "Tablet",
                                      "reorder_level": 30})
    assert created.status_code == 200
    medicine = created.json()
    # Original fields unchanged; selling_price is an addition.
    assert {"id", "name", "strength", "dosage_form", "reorder_level"} <= set(medicine)

    updated = api.put(f"/medicines/{medicine['id']}", {"name": "Metformin", "strength": "500 mg",
                                                       "dosage_form": "Tablet", "reorder_level": 45})
    assert updated.json()["reorder_level"] == 45
    assert db.execute("SELECT reorder_level FROM medicines WHERE id = %s", (medicine["id"],)).fetchone()["reorder_level"] == 45


def test_medicine_validation(api):
    # Previously accepted: negative reorder level on update, blank name, duplicates.
    assert api.put("/medicines/1", {"name": "Paracetamol", "strength": "500 mg", "dosage_form": "Tablet",
                                    "reorder_level": -5}).status_code == 422
    assert api.post("/medicines", {"name": "  ", "strength": "", "dosage_form": ""}).status_code == 422
    duplicate = api.post("/medicines", {"name": "paracetamol", "strength": "500 MG", "dosage_form": "tablet"})
    assert duplicate.status_code == 409
    assert api.put("/medicines/9999", {"name": "X", "reorder_level": 1}).status_code == 404


def test_medicine_with_null_strength_listed(api, db):
    """Previously GET /medicines returned 500 when strength was NULL."""
    db.execute("INSERT INTO medicines (name, reorder_level) VALUES ('Oral Rehydration Salts', 5)")
    db.commit()
    names = [m["name"] for m in api.get("/medicines").json()]
    assert "Oral Rehydration Salts" in names


def test_medicine_search_and_sort(api):
    found = api.get("/medicines", params={"search": "amox"}).json()
    assert [m["name"] for m in found] == ["Amoxicillin"]
    names = [m["name"] for m in api.get("/medicines", params={"sort": "name"}).json()]
    assert names == sorted(names, key=str.lower)


def test_batch_invalid_date_is_client_error(api):
    """Previously a 500 Internal Server Error."""
    response = api.post("/batches", {"medicine_id": 1, "batch_number": "BAD", "quantity": 5,
                                     "expiry_date": "not-a-date"})
    assert response.status_code == 422


def test_create_batch_records_opening_movement(api, db):
    response = api.post("/batches", {"medicine_id": 4, "batch_number": "IBU-T1", "quantity": 40,
                                     "expiry_date": "2030-01-31", "unit_cost": 0.8})
    assert response.status_code == 200
    batch_id = response.json()["id"]
    movement = db.execute("SELECT movement_type, quantity FROM stock_movements WHERE batch_id = %s",
                          (batch_id,)).fetchone()
    assert (movement["movement_type"], movement["quantity"]) == ("RECEIVED", 40)
    assert api.post("/batches", {"medicine_id": 4, "batch_number": "IBU-T1", "quantity": 1,
                                 "expiry_date": "2030-01-31"}).status_code == 409
    assert api.get("/stock-reconciliation").json()["reconciled"]


def test_supplier_create_and_list(api):
    created = api.post("/suppliers", {"name": "Tema Pharma Distributors", "email": "orders@tema.example"})
    assert created.status_code == 200 and created.json()["is_active"] is True
    assert api.post("/suppliers", {"name": "tema pharma distributors"}).status_code == 409
    assert api.post("/suppliers", {"name": "Bad Email Ltd", "email": "not-an-email"}).status_code == 422
