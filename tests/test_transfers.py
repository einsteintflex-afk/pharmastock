# Stock transfers and ward requisitions: approval workflow, FEFO dispatch,
# receipt into destination batches, ledger integrity, location scoping.

from datetime import date, timedelta

import pytest


def _day(offset: int) -> str:
    return (date.today() + timedelta(days=offset)).isoformat()


@pytest.fixture(scope="module")
def site(api, ):
    store = api.post("/locations", {"name": "Central Store", "location_type": "CENTRAL_STORE"})
    ward = api.post("/locations", {"name": "Ward A", "location_type": "WARD"})
    assert store.status_code == 201 and ward.status_code == 201, (store.text, ward.text)
    medicine = api.post("/medicines", {"name": "Ceftriaxone", "strength": "1 g", "dosage_form": "Injection"}).json()
    batches = {}
    for number, offset, qty in [("CEF-LATE", 400, 50), ("CEF-EARLY", 60, 30), ("CEF-EXP", -5, 10)]:
        response = api.post("/batches", {"medicine_id": medicine["id"], "batch_number": number, "quantity": qty,
                                         "expiry_date": _day(offset), "location_id": store.json()["id"],
                                         "unit_cost": 4.0})
        assert response.status_code == 200, response.text
        batches[number] = response.json()["id"]
    return {"store": store.json()["id"], "ward": ward.json()["id"], "medicine": medicine["id"], "batches": batches}


def _requisition(api, site, quantity=40, role="PHARMACIST", **extra):
    return api.post("/transfers", {"request_type": "REQUISITION", "from_location_id": site["store"],
                                   "to_location_id": site["ward"], "priority": "URGENT",
                                   "items": [{"medicine_id": site["medicine"], "quantity": quantity}], **extra},
                    role=role)


def test_full_requisition_lifecycle(api, site, db):
    created = _requisition(api, site, notes="Night shift top-up")
    assert created.status_code == 201, created.text
    transfer = created.json()["transfer"]
    transfer_id = transfer["id"]
    assert transfer["status"] == "REQUESTED" and transfer["transfer_number"].startswith("REQ-")
    item = created.json()["items"][0]
    assert item["available_at_source"] == 80  # expired batch excluded

    # Open notification for approvers.
    open_note = [n for n in api.get("/notifications").json() if n["entity_type"] == "transfer"]
    assert open_note and open_note[0]["resolved_at"] is None

    # Cannot dispatch before approval; technicians cannot approve.
    assert api.post(f"/transfers/{transfer_id}/dispatch").status_code == 400
    assert api.post(f"/transfers/{transfer_id}/approve", {}, role="PHARMACY_TECHNICIAN").status_code == 403

    approved = api.post(f"/transfers/{transfer_id}/approve",
                        {"quantities": {str(item["id"]): 35}, "note": "35 is enough"}, role="MANAGER")
    assert approved.status_code == 200, approved.text
    assert approved.json()["transfer"]["status"] == "APPROVED"
    assert approved.json()["items"][0]["quantity_approved"] == 35

    dispatched = api.post(f"/transfers/{transfer_id}/dispatch", role="STOREKEEPER")
    assert dispatched.status_code == 200, dispatched.text
    batches = dispatched.json()["items"][0]["batches"]
    # FEFO: all 30 of the early batch, then 5 of the late batch.
    assert [(b["batch_number"], b["quantity"]) for b in batches] == [("CEF-EARLY", 30), ("CEF-LATE", 5)]
    source = {r["batch_number"]: r["quantity"] for r in api.get("/inventory", params={
        "medicine_id": site["medicine"], "location_id": site["store"]}).json()}
    assert source == {"CEF-EARLY": 0, "CEF-LATE": 45, "CEF-EXP": 10}

    # In transit: not yet at the ward.
    assert api.get("/inventory", params={"medicine_id": site["medicine"], "location_id": site["ward"]}).json() == []

    received = api.post(f"/transfers/{transfer_id}/receive", role="PHARMACY_TECHNICIAN")
    assert received.status_code == 200, received.text
    assert received.json()["transfer"]["status"] == "RECEIVED"
    ward = {r["batch_number"]: r for r in api.get("/inventory", params={
        "medicine_id": site["medicine"], "location_id": site["ward"]}).json()}
    assert {k: v["quantity"] for k, v in ward.items()} == {"CEF-EARLY": 30, "CEF-LATE": 5}
    # Same expiry and cost travel with the batch.
    assert ward["CEF-EARLY"]["expiry_date"] == _day(60) and float(ward["CEF-EARLY"]["unit_cost"]) == 4.0

    movements = db.execute("SELECT movement_type, SUM(quantity) AS q FROM stock_movements WHERE transfer_id = %s "
                           "GROUP BY movement_type ORDER BY movement_type", (transfer_id,)).fetchall()
    assert [(m["movement_type"], m["q"]) for m in movements] == [("TRANSFER_IN", 35), ("TRANSFER_OUT", 35)]
    assert api.get("/stock-reconciliation").json()["reconciled"] is True
    # Organisation-wide usable stock is unchanged by an internal transfer.
    stock = next(r for r in api.get("/stock-alerts").json() if r["medicine_id"] == site["medicine"])
    assert stock["current_stock"] == 80
    actions = [a["action"] for a in api.get("/audit-log", params={"entity_type": "transfer",
                                                                 "entity_id": str(transfer_id)}).json()]
    assert set(actions) == {"CREATE_REQUISITION", "APPROVE", "DISPATCH", "RECEIVE"}
    # Ledger history on a destination batch shows the transfer in.
    history = api.get(f"/batches/{ward['CEF-EARLY']['batch_id']}").json()
    assert history["reconciled"] and history["history"][0]["movement_type"] == "TRANSFER_IN"


def test_second_transfer_merges_into_existing_destination_batch(api, site):
    transfer = _requisition(api, site, quantity=5).json()
    tid = transfer["transfer"]["id"]
    api.post(f"/transfers/{tid}/approve", {})
    api.post(f"/transfers/{tid}/dispatch")
    assert api.post(f"/transfers/{tid}/receive").status_code == 200
    ward = {r["batch_number"]: r["quantity"] for r in api.get("/inventory", params={
        "medicine_id": site["medicine"], "location_id": site["ward"]}).json()}
    assert ward == {"CEF-EARLY": 30, "CEF-LATE": 10}


def test_insufficient_stock_dispatch_changes_nothing(api, site, db):
    transfer = _requisition(api, site, quantity=500).json()
    tid = transfer["transfer"]["id"]
    api.post(f"/transfers/{tid}/approve", {})
    before = db.execute("SELECT COUNT(*) AS n FROM stock_movements").fetchone()["n"]
    failed = api.post(f"/transfers/{tid}/dispatch")
    assert failed.status_code == 400 and failed.json()["detail"]["available_usable_stock"] == 40
    assert db.execute("SELECT COUNT(*) AS n FROM stock_movements").fetchone()["n"] == before
    assert api.get(f"/transfers/{tid}").json()["transfer"]["status"] == "APPROVED"
    assert api.post(f"/transfers/{tid}/cancel", {"reason": "Not enough stock"}).json()["transfer"]["status"] == \
        "CANCELLED"


def test_reject_and_invalid_transitions(api, site):
    tid = _requisition(api, site, quantity=2).json()["transfer"]["id"]
    rejected = api.post(f"/transfers/{tid}/reject", {"reason": "Use ward stock first"}, role="PHARMACIST")
    assert rejected.json()["transfer"]["status"] == "REJECTED"
    assert rejected.json()["transfer"]["closed_reason"] == "Use ward stock first"
    assert api.post(f"/transfers/{tid}/approve", {}).status_code == 400
    assert api.post(f"/transfers/{tid}/cancel", {"reason": "late"}).status_code == 400


def test_validation(api, site):
    same = api.post("/transfers", {"from_location_id": site["store"], "to_location_id": site["store"],
                                   "items": [{"medicine_id": site["medicine"], "quantity": 1}]})
    assert same.status_code == 400
    duplicate = api.post("/transfers", {"from_location_id": site["store"], "to_location_id": site["ward"],
                                        "items": [{"medicine_id": site["medicine"], "quantity": 1},
                                                  {"medicine_id": site["medicine"], "quantity": 2}]})
    assert duplicate.status_code == 400
    assert api.post("/transfers", {"from_location_id": site["store"], "to_location_id": site["ward"],
                                   "items": []}).status_code == 422
    assert api.post("/transfers", {"from_location_id": site["store"], "to_location_id": site["ward"],
                                   "items": [{"medicine_id": site["medicine"], "quantity": 1}]},
                    role="VIEWER").status_code == 403
    # Transfer movements cannot be recorded by hand.
    manual = api.post("/stock-movements", {"batch_id": site["batches"]["CEF-LATE"], "movement_type": "TRANSFER_OUT",
                                           "quantity": 1})
    assert manual.status_code == 400


def test_location_scoped_user(api, site, db):
    # The technician works on Ward A only.
    db.execute("UPDATE users SET location_id = %s WHERE username = 'pharmacy_technician'", (site["ward"],))
    db.commit()
    try:
        # Can requisition INTO the ward, not transfer OUT of the store.
        assert _requisition(api, site, quantity=1, role="PHARMACY_TECHNICIAN").status_code == 201
        push = api.post("/transfers", {"request_type": "TRANSFER", "from_location_id": site["store"],
                                       "to_location_id": site["ward"],
                                       "items": [{"medicine_id": site["medicine"], "quantity": 1}]},
                        role="PHARMACY_TECHNICIAN")
        assert push.status_code == 403
        # A ward -> store return dispatched by the manager cannot be received by the ward technician.
        back = api.post("/transfers", {"request_type": "TRANSFER", "from_location_id": site["ward"],
                                       "to_location_id": site["store"],
                                       "items": [{"medicine_id": site["medicine"], "quantity": 1}]}).json()
        tid = back["transfer"]["id"]
        api.post(f"/transfers/{tid}/approve", {})
        assert api.post(f"/transfers/{tid}/dispatch").status_code == 200
        assert api.post(f"/transfers/{tid}/receive", role="PHARMACY_TECHNICIAN").status_code == 403
        assert api.post(f"/transfers/{tid}/receive", role="STOREKEEPER").status_code == 200
    finally:
        db.execute("UPDATE users SET location_id = NULL WHERE username = 'pharmacy_technician'")
        db.commit()


def test_separate_approver_setting(api, site):
    assert api.put("/settings", {"transfers.separate_approver": True}).status_code == 200
    tid = _requisition(api, site, quantity=1, role="MANAGER").json()["transfer"]["id"]
    assert api.post(f"/transfers/{tid}/approve", {}, role="MANAGER").status_code == 403
    assert api.post(f"/transfers/{tid}/approve", {}, role="PHARMACIST").status_code == 200
    assert api.put("/settings", {"transfers.separate_approver": "yes"}).status_code == 400
    api.put("/settings", {"transfers.separate_approver": False})


def test_list_and_filters(api, site):
    response = api.get("/transfers", params={"limit": 2})
    assert len(response.json()) == 2 and int(response.headers["x-total-count"]) >= 6
    open_rows = api.get("/transfers", params={"open_only": True}).json()
    assert all(r["status"] in ("REQUESTED", "APPROVED", "DISPATCHED") for r in open_rows)
    received = api.get("/transfers", params={"status": "RECEIVED", "location_id": site["ward"]}).json()
    assert len(received) >= 2 and all(r["status"] == "RECEIVED" for r in received)
    movements = api.get("/stock-movements", params={"movement_type": "TRANSFER_IN"}).json()
    assert movements and all(m["movement_type"] == "TRANSFER_IN" for m in movements)
