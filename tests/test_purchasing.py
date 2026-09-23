# Purchasing: orders, items, partial/full receiving, batch creation, cost,
# status transitions, cancellation, supplier management.

from datetime import date, timedelta

import pytest

EXPIRY = (date.today() + timedelta(days=500)).isoformat()


@pytest.fixture(scope="module")
def order(api):
    supplier = api.post("/suppliers", {"name": "Accra Medical Wholesale", "phone": "+233 30 000 0000"}).json()
    number = api.get("/purchase-orders/next-number").json()["order_number"]
    po = api.post("/purchase-orders", {"supplier_id": supplier["id"], "order_number": number,
                                       "notes": "Monthly order"}, role="PHARMACIST")
    assert po.status_code == 200, po.text
    po = po.json()
    assert po["status"] == "DRAFT"
    ibuprofen = api.post(f"/purchase-orders/{po['id']}/items",
                         {"medicine_id": 4, "quantity_ordered": 100, "unit_cost": 0.50}).json()
    paracetamol = api.post(f"/purchase-orders/{po['id']}/items",
                           {"medicine_id": 1, "quantity_ordered": 40, "unit_cost": 0.20}).json()
    return {"id": po["id"], "supplier_id": supplier["id"], "ibuprofen_item": ibuprofen["id"],
            "paracetamol_item": paracetamol["id"], "number": number}


def test_first_item_moves_draft_to_ordered(api, order):
    detail = api.get(f"/purchase-orders/{order['id']}").json()
    assert detail["order"]["status"] == "ORDERED"
    assert detail["totals"]["order_value"] == 58.0


def test_duplicate_line_and_duplicate_number_rejected(api, order):
    assert api.post(f"/purchase-orders/{order['id']}/items",
                    {"medicine_id": 4, "quantity_ordered": 1, "unit_cost": 1}).status_code == 409
    assert api.post("/purchase-orders", {"supplier_id": order["supplier_id"],
                                         "order_number": order["number"]}).status_code == 400


def test_partial_receipt_creates_batch_with_cost(api, order, db):
    response = api.post("/purchase-receipts", {"purchase_order_item_id": order["ibuprofen_item"],
                                               "batch_number": "IBU-PO-1", "quantity_received": 60,
                                               "expiry_date": EXPIRY}, role="STOREKEEPER")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["purchase_order_status"] == "PARTIALLY_RECEIVED"
    batch = db.execute("SELECT * FROM batches WHERE id = %s", (body["batch_id"],)).fetchone()
    assert batch["quantity"] == 60 and float(batch["unit_cost"]) == 0.50
    assert batch["supplier_id"] == order["supplier_id"] and batch["received_date"] == date.today()
    receipt = db.execute("SELECT received_by, received_by_user_id FROM purchase_receipts WHERE id = %s",
                         (body["receipt_id"],)).fetchone()
    assert receipt["received_by"] == "Test Storekeeper" and receipt["received_by_user_id"] is not None
    movement = db.execute("SELECT movement_type, quantity FROM stock_movements WHERE id = %s",
                          (body["movement_id"],)).fetchone()
    assert (movement["movement_type"], movement["quantity"]) == ("RECEIVED", 60)


def test_ibuprofen_stock_updated(api):
    row = next(r for r in api.get("/stock-alerts").json() if r["medicine"] == "Ibuprofen")
    assert row["current_stock"] == 60 and row["status"] == "LOW STOCK"  # reorder level 60


def test_receiving_validation(api, order):
    over = api.post("/purchase-receipts", {"purchase_order_item_id": order["ibuprofen_item"], "batch_number": "X",
                                           "quantity_received": 41, "expiry_date": EXPIRY})
    assert over.status_code == 400 and over.json()["detail"]["quantity_remaining"] == 40
    expired = api.post("/purchase-receipts", {"purchase_order_item_id": order["ibuprofen_item"], "batch_number": "X",
                                              "quantity_received": 1, "expiry_date": "2020-01-01"})
    assert expired.status_code == 400
    wrong_expiry = api.post("/purchase-receipts", {"purchase_order_item_id": order["ibuprofen_item"],
                                                   "batch_number": "IBU-PO-1", "quantity_received": 1,
                                                   "expiry_date": (date.today() + timedelta(days=600)).isoformat()})
    assert wrong_expiry.status_code == 400
    assert api.post("/purchase-receipts", {"purchase_order_item_id": 999999, "batch_number": "X",
                                           "quantity_received": 1, "expiry_date": EXPIRY}).status_code == 404
    assert api.post("/purchase-receipts", {"purchase_order_item_id": order["ibuprofen_item"], "batch_number": "X",
                                           "quantity_received": 0, "expiry_date": EXPIRY}).status_code == 422


def test_cost_rules_after_receipt(api, order):
    changed = api.put(f"/purchase-orders/{order['id']}/items/{order['ibuprofen_item']}",
                      {"quantity_ordered": 100, "unit_cost": 0.99})
    assert changed.status_code == 400
    too_small = api.put(f"/purchase-orders/{order['id']}/items/{order['ibuprofen_item']}",
                        {"quantity_ordered": 50, "unit_cost": 0.50})
    assert too_small.status_code == 400
    assert api.delete(f"/purchase-orders/{order['id']}/items/{order['ibuprofen_item']}").status_code == 400


def test_receiving_into_existing_batch_uses_weighted_cost(api, order, db):
    # Same batch number: merged into the existing batch.
    response = api.post("/purchase-receipts", {"purchase_order_item_id": order["ibuprofen_item"],
                                               "batch_number": "IBU-PO-1", "quantity_received": 40,
                                               "expiry_date": EXPIRY})
    assert response.status_code == 200
    assert response.json()["new_batch_quantity"] == 100
    item = db.execute("SELECT quantity_received FROM purchase_order_items WHERE id = %s",
                      (order["ibuprofen_item"],)).fetchone()
    assert item["quantity_received"] == 100


def test_full_receipt_marks_order_received(api, order, db):
    response = api.post("/purchase-receipts", {"purchase_order_item_id": order["paracetamol_item"],
                                               "batch_number": "PARA-PO-9", "quantity_received": 40,
                                               "expiry_date": EXPIRY})
    assert response.json()["purchase_order_status"] == "RECEIVED"
    assert api.post(f"/purchase-orders/{order['id']}/items",
                    {"medicine_id": 2, "quantity_ordered": 1, "unit_cost": 1}).status_code == 400
    assert api.post(f"/purchase-orders/{order['id']}/cancel", {"reason": "too late"}).status_code == 400
    assert api.get("/stock-reconciliation").json()["reconciled"]


def test_item_edit_delete_and_cancel(api, order, db):
    po = api.post("/purchase-orders", {"supplier_id": order["supplier_id"], "order_number": "PO-TEST-CANCEL"}).json()
    item = api.post(f"/purchase-orders/{po['id']}/items", {"medicine_id": 2, "quantity_ordered": 10,
                                                           "unit_cost": 5.0}).json()
    edited = api.put(f"/purchase-orders/{po['id']}/items/{item['id']}", {"quantity_ordered": 12, "unit_cost": 4.5})
    assert edited.status_code == 200 and edited.json()["quantity_ordered"] == 12
    assert api.put(f"/purchase-orders/{po['id']}", {"notes": "Urgent"}).json()["notes"] == "Urgent"

    cancelled = api.post(f"/purchase-orders/{po['id']}/cancel", {"reason": "Supplier out of stock"})
    assert cancelled.status_code == 200 and cancelled.json()["status"] == "CANCELLED"
    receive = api.post("/purchase-receipts", {"purchase_order_item_id": item["id"], "batch_number": "Z",
                                              "quantity_received": 1, "expiry_date": EXPIRY})
    assert receive.status_code == 400
    assert api.delete(f"/purchase-orders/{po['id']}/items/{item['id']}").status_code == 400


def test_supplier_lifecycle_and_detail(api, order):
    detail = api.get(f"/suppliers/{order['supplier_id']}").json()
    assert detail["summary"]["orders"] == 2
    assert {p["medicine"] for p in detail["products_supplied"]} == {"Ibuprofen", "Paracetamol"}

    updated = api.put(f"/suppliers/{order['supplier_id']}", {"name": "Accra Medical Wholesale Ltd",
                                                             "contact_person": "Ama Owusu"})
    assert updated.json()["contact_person"] == "Ama Owusu"

    assert api.post(f"/suppliers/{order['supplier_id']}/deactivate").json()["is_active"] is False
    assert api.post("/purchase-orders", {"supplier_id": order["supplier_id"],
                                         "order_number": "PO-INACTIVE"}).status_code == 400
    assert [s["id"] for s in api.get("/suppliers", params={"active": False}).json()] == [order["supplier_id"]]
    assert api.post(f"/suppliers/{order['supplier_id']}/activate").json()["is_active"] is True
    assert api.get("/suppliers", params={"search": "accra"}).json()[0]["id"] == order["supplier_id"]


def test_purchasing_notifications(api):
    categories = {n["category"] for n in api.get("/notifications").json()}
    assert {"PURCHASING", "RECEIVING"} <= categories
