# Stock adjustments (reason codes, thresholds, approval), stock counts,
# purchase order approval, expected delivery / overdue, PO from reorder,
# price history, receipts (discount, tax, PDF, digital link), logo upload,
# onboarding, location scoping.

import io
from datetime import date, timedelta

import psycopg
import pytest

from tests.conftest import TEST_URL


def _day(offset):
    return (date.today() + timedelta(days=offset)).isoformat()


def _ledger_ok(db, batch_id):
    row = db.execute(
        """
        SELECT b.quantity, COALESCE(SUM(CASE WHEN sm.movement_type IN ('RECEIVED', 'RETURNED', 'ADJUSTMENT', 'TRANSFER_IN')
                                   THEN sm.quantity ELSE -sm.quantity END), 0) AS ledger
        FROM batches b LEFT JOIN stock_movements sm ON sm.batch_id = b.id WHERE b.id = %s GROUP BY b.quantity
        """, (batch_id,)).fetchone()
    return row["quantity"] == row["ledger"]


@pytest.fixture(scope="module")
def stock(api):
    med = api.post("/medicines", {"name": "Control Tabs", "strength": "10 mg", "dosage_form": "Tablet",
                                  "reorder_level": 20, "selling_price": 5}).json()
    batch = api.post("/batches", {"medicine_id": med["id"], "batch_number": "CT-1", "quantity": 100,
                                  "expiry_date": _day(400), "unit_cost": 2}).json()
    old = api.post("/batches", {"medicine_id": med["id"], "batch_number": "CT-OLD", "quantity": 10,
                                "expiry_date": _day(200), "unit_cost": 2}).json()
    return {"medicine": med["id"], "batch": batch["id"], "old": old["id"], "location": batch["location_id"]}


def test_adjustment_with_reason_code_posts_and_keeps_ledger(api, stock, db):
    result = api.client.post("/adjustments", json={"batch_id": stock["batch"], "reason_code": "BREAKAGE", "change": -3,
                                                   "notes": "Dropped bottle"},
                             headers={**api.headers("ADMINISTRATOR"), "X-Client-Name": "Counter tablet"})
    assert result.status_code == 201, result.text
    adj = result.json()
    assert adj["status"] == "POSTED" and adj["previous_quantity"] == 100 and adj["new_quantity"] == 97
    assert adj["client_name"] == "Counter tablet" and adj["request_id"]
    movement = db.execute("SELECT movement_type, quantity FROM stock_movements WHERE id = %s",
                          (adj["movement_id"],)).fetchone()
    assert movement == {"movement_type": "DAMAGED", "quantity": 3}
    assert _ledger_ok(db, stock["batch"])


def test_adjustment_validation(api, stock):
    assert api.post("/adjustments", {"batch_id": stock["batch"], "reason_code": "DAMAGE", "change": 5}).status_code == 400
    assert api.post("/adjustments", {"batch_id": stock["batch"], "reason_code": "THEFT_LOSS",
                                     "change": -1}).status_code == 400  # notes required
    assert api.post("/adjustments", {"batch_id": stock["batch"], "reason_code": "EXPIRY_WRITE_OFF",
                                     "change": -1}).status_code == 400  # not expired
    assert api.post("/adjustments", {"batch_id": stock["batch"], "reason_code": "NONSENSE",
                                     "change": -1}).status_code == 422
    assert api.post("/adjustments", {"batch_id": stock["batch"], "reason_code": "OTHER", "change": -1000,
                                     "notes": "x"}).status_code == 400
    assert api.post("/adjustments", {"batch_id": stock["batch"], "reason_code": "PHYSICAL_COUNT", "change": -1},
                    role="VIEWER").status_code == 403


def test_adjustment_above_threshold_needs_another_approver(api, stock, db):
    assert api.put("/settings", {"adjustments.approval_quantity_threshold": 5}).status_code == 200
    pending = api.post("/adjustments", {"batch_id": stock["batch"], "reason_code": "PHYSICAL_COUNT",
                                        "counted_quantity": 87}, role="PHARMACIST").json()
    assert pending["status"] == "PENDING_APPROVAL" and pending["adjustment_quantity"] == -10
    assert db.execute("SELECT quantity FROM batches WHERE id = %s", (stock["batch"],)).fetchone()["quantity"] == 97
    # A sale happens meanwhile; approval applies the delta, not the old count.
    api.post("/dispensations", {"dispense_type": "OTC", "payment_method": "CASH",
                                "items": [{"medicine_id": stock["medicine"], "quantity": 1}]})
    # FEFO took the older batch; quantities stay consistent either way.
    before = db.execute("SELECT quantity FROM batches WHERE id = %s", (stock["batch"],)).fetchone()["quantity"]
    assert api.post(f"/adjustments/{pending['id']}/decision", {"approve": True},
                    role="PHARMACIST").status_code == 403  # pharmacist lacks stock.approve
    assert api.post(f"/adjustments/{pending['id']}/decision", {"approve": False}, role="MANAGER").status_code == 400
    approved = api.post(f"/adjustments/{pending['id']}/decision", {"approve": True, "note": "Checked"}, role="MANAGER")
    assert approved.status_code == 200 and approved.json()["status"] == "POSTED"
    assert db.execute("SELECT quantity FROM batches WHERE id = %s", (stock["batch"],)).fetchone()["quantity"] == before - 10
    assert _ledger_ok(db, stock["batch"])
    again = api.post(f"/adjustments/{pending['id']}/decision", {"approve": True}, role="MANAGER")
    assert again.status_code == 409
    # The requester can never approve their own adjustment.
    own = api.post("/adjustments", {"batch_id": stock["batch"], "reason_code": "PHYSICAL_COUNT", "change": -6},
                   role="MANAGER").json()
    assert api.post(f"/adjustments/{own['id']}/decision", {"approve": True}, role="MANAGER").status_code == 403
    assert api.post(f"/adjustments/{own['id']}/cancel", role="MANAGER").json()["status"] == "CANCELLED"
    api.put("/settings", {"adjustments.approval_quantity_threshold": 0})


def test_legacy_adjustment_endpoint_records_an_adjustment(api, stock, db):
    current = db.execute("SELECT quantity FROM batches WHERE id = %s", (stock["batch"],)).fetchone()["quantity"]
    legacy = api.post("/stock-movements", {"batch_id": stock["batch"], "movement_type": "ADJUSTMENT",
                                           "quantity": current - 2, "reason": "Shelf count"})
    assert legacy.status_code == 200, legacy.text
    body = legacy.json()
    assert body["quantity"] == -2 and body["new_batch_quantity"] == current - 2
    assert body["adjustment"]["reason_code"] == "PHYSICAL_COUNT"
    listed = api.get("/adjustments", params={"reason_code": "PHYSICAL_COUNT"}).json()
    assert any(a["id"] == body["adjustment"]["id"] for a in listed)


def test_stock_count_full_cycle(api, stock, db):
    count = api.post("/stock-counts", {"location_id": stock["location"], "name": "Monthly count"},
                     role="STOREKEEPER")
    assert count.status_code == 201, count.text
    cid = count.json()["id"]
    assert count.json()["count_number"].startswith("CNT-")
    system = db.execute("SELECT quantity FROM batches WHERE id = %s", (stock["batch"],)).fetchone()["quantity"]
    # Scanning pack by pack adds up.
    api.post(f"/stock-counts/{cid}/lines", {"batch_id": stock["batch"], "counted_quantity": system - 5, "mode": "set"},
             role="STOREKEEPER")
    detail = api.post(f"/stock-counts/{cid}/lines", {"batch_id": stock["batch"], "counted_quantity": 1, "mode": "add"},
                      role="STOREKEEPER").json()
    line = detail["lines"][0]
    assert line["system_quantity"] == system and line["counted_quantity"] == system - 4 and line["variance"] == -4
    assert line["variance_value"] == -8.0
    assert detail["summary"]["units_short"] == 4 and detail["summary"]["uncounted_batches"] >= 1
    # A sale after counting the shelf is not overwritten by posting.
    api.post("/dispensations", {"dispense_type": "OTC", "payment_method": "CASH",
                                "items": [{"medicine_id": stock["medicine"], "quantity": 20}]})
    after_sale = db.execute("SELECT quantity FROM batches WHERE id = %s", (stock["batch"],)).fetchone()["quantity"]
    assert api.post(f"/stock-counts/{cid}/post", role="MANAGER").status_code == 409  # not submitted
    assert api.post(f"/stock-counts/{cid}/submit", role="STOREKEEPER").json()["status"] == "SUBMITTED"
    assert api.post(f"/stock-counts/{cid}/lines", {"batch_id": stock["batch"], "counted_quantity": 1},
                    role="STOREKEEPER").status_code == 409
    assert api.post(f"/stock-counts/{cid}/post", role="STOREKEEPER").status_code == 403  # needs stock.approve
    posted = api.post(f"/stock-counts/{cid}/post", role="MANAGER")
    assert posted.status_code == 200 and posted.json()["status"] == "POSTED"
    assert db.execute("SELECT quantity FROM batches WHERE id = %s", (stock["batch"],)).fetchone()["quantity"] == after_sale - 4
    assert _ledger_ok(db, stock["batch"])
    assert posted.json()["lines"][0]["adjustment_id"]
    assert api.post(f"/stock-counts/{cid}/post", role="MANAGER").status_code == 409  # posts once
    assert api.post(f"/stock-counts/{cid}/cancel", {"reason": "late"}, role="MANAGER").status_code == 409


def test_count_rejects_batches_from_another_location(api, stock):
    other = api.post("/locations", {"name": "Count Store", "location_type": "STORE"}).json()
    cid = api.post("/stock-counts", {"location_id": other["id"]}).json()["id"]
    assert api.post(f"/stock-counts/{cid}/lines", {"batch_id": stock["batch"], "counted_quantity": 1}).status_code == 400
    assert api.post(f"/stock-counts/{cid}/submit").status_code == 400  # nothing counted


def test_purchase_order_approval_workflow(api, stock):
    supplier = api.post("/suppliers", {"name": "Approval Supplies"}).json()
    assert api.put("/settings", {"purchasing.approval_required": True}).status_code == 200
    order = api.post("/purchase-orders", {"supplier_id": supplier["id"], "order_number": "PO-APPR-1",
                                          "expected_delivery_date": _day(-2)}, role="PHARMACIST").json()
    api.post(f"/purchase-orders/{order['id']}/items", {"medicine_id": stock["medicine"], "quantity_ordered": 50,
                                                       "unit_cost": 2.5}, role="PHARMACIST")
    detail = api.get(f"/purchase-orders/{order['id']}").json()
    assert detail["order"]["status"] == "DRAFT" and detail["approval_required"] is True
    item_id = detail["items"][0]["id"]
    receive = {"purchase_order_item_id": item_id, "batch_number": "APPR-1", "quantity_received": 10,
               "expiry_date": _day(500)}
    assert api.post("/purchase-receipts", receive).status_code == 400  # not approved
    assert api.post(f"/purchase-orders/{order['id']}/submit", role="PHARMACIST").json()["order"]["status"] == "SUBMITTED"
    assert api.post(f"/purchase-orders/{order['id']}/items", {"medicine_id": stock["medicine"], "quantity_ordered": 1,
                                                              "unit_cost": 1}).status_code == 400
    assert api.post(f"/purchase-orders/{order['id']}/approve", {}, role="PHARMACIST").status_code == 403
    approved = api.post(f"/purchase-orders/{order['id']}/approve", {"note": "Budget ok"}, role="MANAGER")
    assert approved.status_code == 200 and approved.json()["order"]["approved_by_name"] == "Test Manager"
    ordered = api.post(f"/purchase-orders/{order['id']}/mark-ordered").json()
    assert ordered["order"]["status"] == "ORDERED" and ordered["order"]["overdue"] is True
    overdue = api.get("/purchase-orders", params={"overdue": True}).json()
    assert [o["order_number"] for o in overdue] == ["PO-APPR-1"]
    assert api.post("/purchase-receipts", receive).status_code == 200
    api.put("/settings", {"purchasing.approval_required": False})


def test_purchase_order_from_reorder_uses_last_price(api, stock):
    supplier = api.get("/suppliers").json()[0]
    fresh = api.post("/medicines", {"name": "Never Bought", "strength": "1 g", "dosage_form": "Powder",
                                    "reorder_level": 1}).json()
    missing = api.post("/purchase-orders/from-reorder", {"supplier_id": supplier["id"], "items": [
        {"medicine_id": fresh["id"], "quantity": 5}]})
    assert missing.status_code == 400 and "Never Bought" in str(missing.json())
    created = api.post("/purchase-orders/from-reorder", {"supplier_id": supplier["id"], "expected_delivery_date": _day(7),
                                                         "items": [{"medicine_id": stock["medicine"], "quantity": 30},
                                                                   {"medicine_id": fresh["id"], "quantity": 5,
                                                                    "unit_cost": 9.5}]})
    assert created.status_code == 201, created.text
    items = {i["medicine_id"]: i for i in created.json()["items"]}
    assert items[stock["medicine"]]["unit_cost"] == 2.5 and items[fresh["id"]]["unit_cost"] == 9.5
    history = api.get("/price-history", params={"medicine_id": stock["medicine"]}).json()
    assert history and all(h["medicine_id"] == stock["medicine"] for h in history)


def test_receipt_discount_tax_and_digital_link(api, stock, db):
    api.put("/settings", {"receipt.tax_rate_percent": 10, "receipt.tax_label": "VAT", "company.email": "shop@example.com"})
    sale = api.post("/dispensations", {
        "dispense_type": "OTC", "payment_method": "CASH", "patient_name": "Ama <b>Mensah</b>",
        "patient_phone": "+233200000001", "discount_percent": 10,
        "consent": {"whatsapp": True}, "items": [{"medicine_id": stock["medicine"], "quantity": 4}]})
    assert sale.status_code == 201, sale.text
    s = sale.json()
    # 4 x 5.00 = 20.00; 10 % discount = 2.00; VAT 10 % of 18.00 = 1.80; total 19.80
    assert (float(s["subtotal_amount"]), float(s["discount_amount"]), float(s["tax_amount"]),
            float(s["total_amount"]), s["tax_label"]) == (20.0, 2.0, 1.8, 19.8, "VAT")
    consent = db.execute("SELECT status, source FROM message_consents WHERE phone = '+233200000001'").fetchone()
    assert consent == {"status": "OPTED_IN", "source": "COUNTER"}

    link = api.get(f"/dispensations/{s['id']}/receipt-link").json()["url"]
    token = link.rsplit("/", 1)[1]
    page = api.client.get(f"/r/{token}")
    assert page.status_code == 200 and "19.80" in page.text and "Powered by MedCart Tech" in page.text
    assert "<b>Mensah" not in page.text  # customer data escaped / not shown raw
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert api.client.get("/r/" + "0" * 32).status_code == 404
    assert api.client.get("/r/not-a-token").status_code == 404
    with psycopg.connect(TEST_URL) as conn:
        conn.execute("UPDATE receipt_links SET expires_at = CURRENT_TIMESTAMP - interval '1 day' WHERE token = %s", (token,))
        conn.commit()
    assert api.client.get(f"/r/{token}").status_code == 404

    for layout in ("a4", "thermal"):
        pdf = api.get(f"/dispensations/{s['id']}/receipt.pdf", params={"layout": layout})
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert api.post("/dispensations", {"dispense_type": "OTC", "payment_method": "CASH", "discount_amount": 1000,
                                       "items": [{"medicine_id": stock["medicine"], "quantity": 1}]}).status_code == 400
    assert api.post("/dispensations", {"dispense_type": "OTC", "payment_method": "CASH", "consent": {"sms": True},
                                       "items": [{"medicine_id": stock["medicine"], "quantity": 1}]}).status_code == 400
    api.put("/settings", {"receipt.tax_rate_percent": 0})


def _png(size=(40, 20), fmt="PNG"):
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", size, (13, 59, 102)).save(out, format=fmt)
    return out.getvalue()


def test_logo_upload_is_validated_and_reencoded(api):
    headers = {**api.headers("ADMINISTRATOR"), "Content-Type": "image/png"}
    assert api.client.put("/branding/logo", content=b"<svg onload=alert(1)>", headers=headers).status_code == 415
    assert api.client.put("/branding/logo", content=b"GIF89a....", headers={**headers, "Content-Type": "image/gif"}
                          ).status_code == 415
    too_big = api.client.put("/branding/logo", content=b"0" * (2 * 1024 * 1024 + 1), headers=headers)
    assert too_big.status_code == 413
    broken = bytearray(_png())
    broken[40] ^= 0xFF  # corrupt the image data (bad checksum)
    assert api.client.put("/branding/logo", content=bytes(broken), headers=headers).status_code == 415
    polyglot = _png() + b"<script>alert(1)</script>"
    ok = api.client.put("/branding/logo", content=polyglot, headers=headers)
    assert ok.status_code == 200, ok.text
    stored = api.get("/branding/logo")
    assert stored.headers["content-type"] == "image/png" and b"<script>" not in stored.content
    jpeg = api.client.put("/branding/logo", content=_png((1200, 300), "JPEG"),
                          headers={**headers, "Content-Type": "image/jpeg"})
    assert jpeg.json()["width"] == 600  # downscaled
    assert api.get("/branding").json()["has_logo"] is True
    assert api.client.put("/branding/logo", content=_png(), headers={**api.headers("PHARMACIST"),
                                                                     "Content-Type": "image/png"}).status_code == 403
    report = api.get("/reports/inventory", params={"format": "pdf"})
    assert report.status_code == 200 and report.content.startswith(b"%PDF")


def test_onboarding_status_reflects_real_data(api):
    status = api.get("/onboarding").json()
    assert status["total"] == 9 and status["completed_at"]  # existing organization: already set up
    steps = {s["key"]: s for s in status["steps"]}
    assert steps["medicines"]["done"] is True and steps["logo"]["done"] is True


def test_location_scoped_staff(api, stock, db):
    ward = api.post("/locations", {"name": "Scoped Ward", "location_type": "WARD"}).json()
    tech = next(u for u in api.get("/users").json() if u["username"] == "pharmacy_technician")
    api.put(f"/users/{tech['id']}", {"full_name": tech["full_name"], "role": tech["role"], "is_active": True,
                                     "location_id": ward["id"]})
    api.tokens.pop("PHARMACY_TECHNICIAN", None)
    refused = api.post("/dispensations", {"dispense_type": "OTC", "payment_method": "CASH",
                                          "location_id": stock["location"],
                                          "items": [{"medicine_id": stock["medicine"], "quantity": 1}]},
                       role="PHARMACY_TECHNICIAN")
    assert refused.status_code == 403
    # Without a location the sale is taken from the assigned ward (no stock there).
    empty = api.post("/dispensations", {"dispense_type": "OTC", "payment_method": "CASH",
                                        "items": [{"medicine_id": stock["medicine"], "quantity": 1}]},
                     role="PHARMACY_TECHNICIAN")
    assert empty.status_code == 400
    api.put(f"/users/{tech['id']}", {"full_name": tech["full_name"], "role": tech["role"], "is_active": True,
                                     "location_id": None})
    api.tokens.pop("PHARMACY_TECHNICIAN", None)
