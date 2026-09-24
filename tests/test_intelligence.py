# Command center: what needs attention, daily brief, safety stock and reorder
# point, fast / dead movers, stock-out risk, global search, new reports, AI
# tools restricted to what the user may see.

from datetime import date, timedelta

import pytest

from backend.services import assistant


def _day(offset):
    return (date.today() + timedelta(days=offset)).isoformat()


@pytest.fixture(scope="module")
def shop(api, db_module):
    fast = api.post("/medicines", {"name": "Brief Fast Tabs", "strength": "500 mg", "dosage_form": "Tablet",
                                   "reorder_level": 10, "selling_price": 1}).json()
    idle = api.post("/medicines", {"name": "Brief Idle Cream", "strength": "15 g", "dosage_form": "Cream",
                                   "reorder_level": 1}).json()
    empty = api.post("/medicines", {"name": "Brief Empty Syrup", "strength": "100 ml", "dosage_form": "Syrup",
                                    "reorder_level": 5}).json()
    fb = api.post("/batches", {"medicine_id": fast["id"], "batch_number": "BF-1", "quantity": 60,
                               "expiry_date": _day(300), "unit_cost": 0.5}).json()
    api.post("/batches", {"medicine_id": idle["id"], "batch_number": "BI-1", "quantity": 40,
                          "expiry_date": _day(600), "unit_cost": 3}).json()
    api.post("/batches", {"medicine_id": empty["id"], "batch_number": "BE-X", "quantity": 4,
                          "expiry_date": _day(-3), "unit_cost": 2}).json()
    # 40 units dispensed over the last 10 days (backdated): 4 / day over 30 days window -> 1.333 / day.
    sale = api.post("/dispensations", {"dispense_type": "OTC", "payment_method": "CASH",
                                       "items": [{"medicine_id": fast["id"], "quantity": 40}]}).json()
    db_module.execute("UPDATE stock_movements SET movement_date = CURRENT_TIMESTAMP - interval '1 day' "
                      "WHERE dispensation_item_id IN (SELECT id FROM dispensation_items WHERE dispensation_id = %s)",
                      (sale["id"],))
    db_module.execute("UPDATE dispensations SET dispensed_at = CURRENT_TIMESTAMP - interval '1 day' WHERE id = %s",
                      (sale["id"],))
    db_module.commit()
    return {"fast": fast["id"], "idle": idle["id"], "empty": empty["id"], "fast_batch": fb["id"]}


@pytest.fixture(scope="module")
def db_module(api):
    from psycopg.rows import dict_row

    from tests.conftest import org_connection
    with org_connection(1, row_factory=dict_row) as conn:
        yield conn


def test_reorder_has_safety_stock_and_reorder_point(api, shop):
    rows = {r["medicine_id"]: r for r in api.get("/analytics/reorder").json()}
    fast = rows[shop["fast"]]
    daily = fast["average_daily_consumption"]
    assert daily == pytest.approx(40 / 30, rel=0.01)
    assert fast["safety_stock"] == 10  # ceil(1.333 * 7)
    assert fast["reorder_point"] == 19 + 10  # ceil(1.333 * 14) + safety
    assert fast["usable_stock"] == 20 and fast["reorder_recommended"] is True
    assert "reorder point" in fast["reason"]
    assert rows[shop["idle"]]["reorder_point"] is None  # no consumption: no rate-based point


def test_attention_list_is_actionable_and_permission_aware(api, shop):
    items = {i["key"]: i for i in api.get("/attention").json()}
    assert items["expired"]["severity"] == "CRITICAL" and items["expired"]["link"].startswith("#/")
    assert "out_of_stock" in items and "Brief Empty Syrup" in items["out_of_stock"]["detail"]
    assert "reorder" in items
    viewer = {i["key"] for i in api.get("/attention", role="VIEWER").json()}
    assert "reorder" not in viewer and "reconciliation" not in viewer  # no purchasing / adjust rights
    severities = [i["severity"] for i in api.get("/attention").json()]
    assert severities == sorted(severities, key=["CRITICAL", "WARNING", "INFO"].index)


def test_daily_brief_uses_recorded_sales(api, shop):
    brief = api.get("/daily-brief").json()
    assert brief["yesterday"]["transactions"] >= 1 and brief["yesterday"]["sales"] >= 40
    assert brief["summary"][0].startswith("Yesterday:")
    assert any(r["medicine"] == "Brief Fast Tabs" for r in brief["stockout_risk"]) is False  # 20 left / 1.33 = 15 days
    assert api.get("/daily-brief", role="CASHIER").status_code == 403


def test_movers_fast_and_dead(api, shop):
    data = api.get("/analytics/movers").json()
    assert any(r["medicine_id"] == shop["fast"] for r in data["fast"])
    dead = {r["medicine_id"]: r for r in data["dead"]}
    assert shop["idle"] in dead and dead[shop["idle"]]["stock_value"] == 120.0
    assert shop["fast"] not in dead


def test_stockout_risk_counts_only_deliveries_in_time(api, shop):
    risky = {r["medicine_id"] for r in api.get("/analytics/stockout-risk", params={"days": 20}).json()}
    assert shop["fast"] in risky
    supplier = api.post("/suppliers", {"name": "Brief Supplier"}).json()
    order = api.post("/purchase-orders/from-reorder", {"supplier_id": supplier["id"], "expected_delivery_date": _day(2),
                                                       "items": [{"medicine_id": shop["fast"], "quantity": 100,
                                                                  "unit_cost": 0.5}]}).json()
    assert order["order"]["status"] == "DRAFT"
    api.post(f"/purchase-orders/{order['order']['id']}/items", {"medicine_id": shop["idle"], "quantity_ordered": 1,
                                                                 "unit_cost": 3})  # moves the draft to ORDERED
    risky = {r["medicine_id"] for r in api.get("/analytics/stockout-risk", params={"days": 20}).json()}
    assert shop["fast"] not in risky


def test_global_search_respects_permissions(api, shop):
    found = api.get("/search", params={"q": "Brief"}).json()
    assert {m["id"] for m in found["results"]["medicines"]} >= {shop["fast"], shop["idle"]}
    assert found["results"]["batches"] == [] or all("medicine" in b for b in found["results"]["batches"])
    assert "users" in api.get("/search", params={"q": "test"}).json()["results"]
    viewer = api.get("/search", params={"q": "test"}, role="VIEWER").json()["results"]
    assert "users" not in viewer and "dispensations" not in viewer
    assert api.get("/search", params={"q": "B"}).status_code == 422


@pytest.mark.parametrize("key", ["out-of-stock", "fast-moving", "stock-counts", "adjustments"])
def test_new_reports(api, shop, key):
    for fmt in ("json", "csv", "pdf"):
        response = api.get(f"/reports/{key}", params={"format": fmt})
        assert response.status_code == 200, (key, fmt, response.text[:200])
    body = api.get(f"/reports/{key}").json()
    if key == "out-of-stock":
        assert any(r["medicine"] == "Brief Empty Syrup" for r in body["rows"])


def test_assistant_tools_follow_permissions(api, shop):
    viewer_tools = set(api.get("/assistant/status", role="VIEWER").json()["tools"])
    admin_tools = set(api.get("/assistant/status").json()["tools"])
    assert "unusual_adjustments" in admin_tools and "unusual_adjustments" not in viewer_tools
    answer = api.post("/assistant/ask", {"question": "Show unusual adjustments to review"}, role="VIEWER").json()
    assert "audit access" in answer["answer"]
    priorities = api.post("/assistant/ask", {"question": "What are today's priorities?"}).json()
    assert priorities["tools_used"] == ["todays_priorities"] and "Expired stock" in priorities["answer"]
    week = api.post("/assistant/ask", {"question": "What will run out this week?"}).json()
    assert week["tools_used"] == ["stockout_risk_week"]
    history = api.post("/assistant/ask", {"question": "Show the history of Brief Fast Tabs"}).json()
    assert history["tools_used"] == ["medicine_history"] and "dispensed 40" in history["answer"]
    outstanding = api.post("/assistant/ask", {"question": "Which supplier orders are outstanding?"}).json()
    assert outstanding["tools_used"] == ["supplier_outstanding_orders"] and "Brief Supplier" in outstanding["answer"]


def test_run_tool_refuses_tools_outside_the_role(api):
    from backend.security import CurrentUser
    viewer = CurrentUser(id=1, username="v", full_name="V", role="VIEWER", must_change_password=False, token="",
                         ip=None, permissions={"inventory.read"}, features={"core"})
    result = assistant.run_tool(None, "unusual_adjustments", {}, viewer)
    assert "does not allow" in result["error"]
    assert "unusual_adjustments" not in {t["name"] for t in assistant.tool_definitions(viewer)}
