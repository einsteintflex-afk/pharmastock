# Role permissions are enforced by the API, not only hidden in the UI.

import pytest

MEDICINE = {"name": "PermTest", "strength": "1 mg", "dosage_form": "Tablet", "reorder_level": 5}


@pytest.mark.parametrize("role,expected", [
    ("VIEWER", 403), ("STOREKEEPER", 403), ("PHARMACY_TECHNICIAN", 403),
    ("PHARMACIST", 200), ("MANAGER", 200), ("ADMINISTRATOR", 200),
])
def test_create_medicine(api, role, expected):
    body = {**MEDICINE, "name": f"PermTest {role}"}
    assert api.post("/medicines", body, role=role).status_code == expected


@pytest.mark.parametrize("role", ["VIEWER", "STOREKEEPER", "PHARMACY_TECHNICIAN", "PHARMACIST", "MANAGER"])
def test_only_admin_manages_users(api, role):
    assert api.get("/users", role=role).status_code == 403


@pytest.mark.parametrize("role,expected", [("VIEWER", 403), ("PHARMACIST", 403), ("MANAGER", 200), ("ADMINISTRATOR", 200)])
def test_audit_log_access(api, role, expected):
    assert api.get("/audit-log", role=role).status_code == expected


@pytest.mark.parametrize("role,expected", [
    ("VIEWER", 403), ("STOREKEEPER", 403), ("PHARMACY_TECHNICIAN", 200), ("PHARMACIST", 200),
])
def test_dispense(api, role, expected):
    # Amoxicillin (id 2) has usable stock.
    response = api.post("/dispense", {"medicine_id": 2, "quantity": 1}, role=role)
    assert response.status_code == expected, response.text


def test_movement_type_permissions(api):
    # Technician may dispense but not adjust stock.
    batch = next(b for b in api.get("/inventory").json() if b["batch_number"] == "AMOX002")
    denied = api.post("/stock-movements", {"batch_id": batch["batch_id"], "movement_type": "DAMAGED",
                                           "quantity": 1, "reason": "broken"}, role="PHARMACY_TECHNICIAN")
    assert denied.status_code == 403
    allowed = api.post("/stock-movements", {"batch_id": batch["batch_id"], "movement_type": "DAMAGED",
                                            "quantity": 1, "reason": "broken"}, role="STOREKEEPER")
    assert allowed.status_code == 200


def test_purchasing_permissions(api):
    body = {"supplier_id": 1, "order_number": "PO-PERM-1"}
    assert api.post("/purchase-orders", body, role="STOREKEEPER").status_code == 403
    assert api.post("/purchase-orders", body, role="PHARMACIST").status_code == 200


def test_viewer_can_read_but_not_export(api):
    assert api.get("/reports/inventory", role="VIEWER").status_code == 200
    assert api.get("/reports/inventory", params={"format": "csv"}, role="VIEWER").status_code == 403
    assert api.get("/reports/inventory", params={"format": "csv"}, role="STOREKEEPER").status_code == 200


def test_settings_permissions(api):
    assert api.put("/settings", {"reorder.lead_time_days": 10}, role="PHARMACIST").status_code == 403
    assert api.put("/settings", {"reorder.lead_time_days": 14}, role="MANAGER").status_code == 200
