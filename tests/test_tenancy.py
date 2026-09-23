# Multi-organization tenancy: database-enforced isolation, plans, limits,
# platform administration.

import psycopg
import pytest

from tests.conftest import TEST_URL, org_connection

ADMIN2 = {"username": "org2admin", "password": "Second-org-pass-9"}


@pytest.fixture(scope="module")
def org2(api):
    created = api.post("/platform/organizations", {
        "name": "Second Pharmacy", "org_type": "COMMUNITY_PHARMACY", "plan": "BASIC", "status": "ACTIVE",
        "admin_username": ADMIN2["username"], "admin_full_name": "Second Admin", "admin_password": ADMIN2["password"],
    })
    assert created.status_code == 201, created.text
    org_id = created.json()["organization"]["id"]
    token = api.client.post("/auth/login", json=ADMIN2).json()["token"]
    api.client.cookies.clear()
    headers = {"Authorization": f"Bearer {token}"}
    changed = api.client.post("/auth/change-password", headers=headers,
                              json={"current_password": ADMIN2["password"], "new_password": "Second-org-new-7"})
    assert changed.status_code == 200
    return {"id": org_id, "headers": headers}


def get2(api, org2, path, **kw):
    return api.client.get(path, headers=org2["headers"], **kw)


def post2(api, org2, path, body):
    return api.client.post(path, json=body, headers=org2["headers"])


def test_new_organization_is_seeded_and_empty(api, org2):
    me = get2(api, org2, "/auth/me").json()
    assert me["organization"]["name"] == "Second Pharmacy" and me["organization"]["plan"] == "BASIC"
    assert get2(api, org2, "/medicines").json() == []
    assert get2(api, org2, "/inventory").json() == []
    assert get2(api, org2, "/suppliers").json() == []
    assert [l["name"] for l in get2(api, org2, "/locations").json()] == ["Main Pharmacy"]
    settings = {s["key"]: s["value"] for s in get2(api, org2, "/settings").json()}
    assert settings["pharmacy.name"] == "Second Pharmacy" and settings["expiry.critical_days"] == 30


def test_each_organization_sees_only_its_own_data(api, org2):
    # Same medicine name is allowed in another organization.
    created = post2(api, org2, "/medicines", {"name": "Paracetamol", "strength": "500 mg", "dosage_form": "Tablet"})
    assert created.status_code == 200
    org2_medicine = created.json()["id"]
    assert [m["name"] for m in get2(api, org2, "/medicines").json()] == ["Paracetamol"]
    assert org2_medicine not in [m["id"] for m in api.get("/medicines").json()]
    # Organization 1 still has its original medicines untouched.
    assert len(api.get("/medicines").json()) == 3


def test_cross_tenant_ids_are_not_found(api, org2):
    # Org 1's batch 1 / medicine 1 / purchase order 1 do not exist for org 2.
    assert get2(api, org2, "/batches/1").status_code == 404
    assert get2(api, org2, "/medicines/1").status_code == 404
    assert get2(api, org2, "/purchase-orders/1").status_code == 404
    assert post2(api, org2, "/dispense", {"medicine_id": 1, "quantity": 1}).status_code == 404
    assert post2(api, org2, "/stock-movements", {"batch_id": 1, "movement_type": "DISPENSED",
                                                 "quantity": 1}).status_code == 404
    assert post2(api, org2, "/batches", {"medicine_id": 1, "batch_number": "X", "quantity": 1,
                                         "expiry_date": "2030-01-01"}).status_code == 404
    # Org 1's audit trail and users are invisible to org 2.
    users = get2(api, org2, "/users").json()
    assert [u["username"] for u in users] == [ADMIN2["username"]]
    assert all(r["entity_type"] != "medicine" or r["entity_id"] == str(created_id(api, org2))
               for r in get2(api, org2, "/audit-log").json())
    assert get2(api, org2, "/stock-reconciliation").json()["reconciled"] is True


def created_id(api, org2):
    return get2(api, org2, "/medicines").json()[0]["id"]


def test_database_enforces_isolation_without_application_filters():
    # Raw SQL with no WHERE clause: row level security scopes every query.
    with org_connection(1) as conn:
        org1 = conn.execute("SELECT COUNT(*) FROM medicines").fetchone()[0]
    with org_connection(2) as conn:
        org2 = conn.execute("SELECT COUNT(*) FROM medicines").fetchone()[0]
        # Writing a row into another organization is rejected.
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO medicines (name, organization_id) VALUES ('Intruder', 1)")
    assert org1 >= 3 and org2 == 1
    # No organization set: nothing is visible and inserts fail.
    with psycopg.connect(TEST_URL) as conn:
        assert conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
        with pytest.raises((psycopg.errors.NotNullViolation, psycopg.errors.InsufficientPrivilege)):
            conn.execute("INSERT INTO medicines (name) VALUES ('Orphan')")


def test_plan_limits_and_features(api, org2):
    # BASIC: one location, no AI assistant, no advanced analytics.
    second = post2(api, org2, "/locations", {"name": "Branch 2", "location_type": "BRANCH"})
    assert second.status_code == 403 and "plan" in second.json()["detail"]
    assert post2(api, org2, "/assistant/ask", {"question": "What needs reordering?"}).status_code == 403
    assert get2(api, org2, "/analytics/forecast").status_code == 403
    info = get2(api, org2, "/organization").json()
    assert info["limits"]["max_locations"] == 1 and "ai_assistant" not in info["features"]
    # Upgrading the plan enables them.
    assert api.put(f"/platform/organizations/{org2['id']}",
                   {"plan": "PROFESSIONAL", "status": "ACTIVE", "limits": {}}).status_code == 200
    token = api.client.post("/auth/login", json={"username": ADMIN2["username"], "password": "Second-org-new-7"}).json()["token"]
    api.client.cookies.clear()
    org2["headers"] = {"Authorization": f"Bearer {token}"}
    assert post2(api, org2, "/locations", {"name": "Branch 2", "location_type": "BRANCH"}).status_code == 201
    assert get2(api, org2, "/analytics/forecast").status_code == 200


def test_limit_override_per_organization(api, org2):
    assert api.put(f"/platform/organizations/{org2['id']}", {"plan": "PROFESSIONAL", "status": "ACTIVE",
                                                            "limits": {"max_users": 1}}).status_code == 200
    blocked = post2(api, org2, "/users", {"username": "org2tech", "full_name": "Tech", "role": "VIEWER",
                                          "password": "Viewer-pass-2026"})
    assert blocked.status_code == 403
    assert api.put(f"/platform/organizations/{org2['id']}", {"plan": "PROFESSIONAL", "status": "ACTIVE",
                                                            "limits": {"bogus": 1}}).status_code == 400


def test_platform_admin_only(api, org2):
    assert api.get("/platform/organizations", role="MANAGER").status_code == 403
    assert get2(api, org2, "/platform/organizations").status_code == 403
    rows = api.get("/platform/organizations").json()
    assert {r["name"] for r in rows} >= {"Second Pharmacy"} and len(rows) >= 2


def test_suspended_organization_is_locked_out(api, org2):
    assert api.put(f"/platform/organizations/{org2['id']}",
                   {"plan": "PROFESSIONAL", "status": "SUSPENDED", "limits": {}}).status_code == 200
    assert get2(api, org2, "/medicines").status_code == 401
    login = api.client.post("/auth/login", json={"username": ADMIN2["username"], "password": "Second-org-new-7"})
    assert login.status_code == 403
    # Platform admin cannot suspend their own organization.
    assert api.put("/platform/organizations/1", {"plan": "ENTERPRISE", "status": "SUSPENDED",
                                                 "limits": {}}).status_code == 400
    # Organization 1 unaffected.
    assert api.get("/medicines").status_code == 200
