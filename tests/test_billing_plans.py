# Plan catalogue in the database, entitlements and limits enforced on the
# API, subscriptions, payments (verified webhooks only), messaging credits.

import hashlib
import hmac
import json

import psycopg
import pytest

from tests.conftest import TEST_URL

OWNER = {"username": "billowner", "password": "Bill-owner-pass-1"}


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def shop(api):
    created = api.post("/platform/organizations", {
        "name": "Billing Pharmacy", "plan": "BASIC", "status": "TRIAL",
        "admin_username": OWNER["username"], "admin_full_name": "Bill Owner", "admin_password": OWNER["password"]})
    assert created.status_code == 201, created.text
    token = api.client.post("/auth/login", json=OWNER).json()["token"]
    api.client.cookies.clear()
    api.client.post("/auth/change-password", headers=_bearer(token),
                    json={"current_password": OWNER["password"], "new_password": "Bill-owner-new-2"})
    return {"id": created.json()["organization"]["id"], "headers": _bearer(token)}


def test_plans_differ_and_come_from_the_database(api):
    catalogue = api.get("/plans").json()["plans"]
    basic, pro, ent = (set(catalogue[c]["features"]) for c in ("BASIC", "PROFESSIONAL", "ENTERPRISE"))
    assert basic < pro < ent
    assert "ai_assistant" not in basic and "whatsapp" in pro and "hospital" in ent
    assert catalogue["BASIC"]["limits"]["max_locations"] == 1
    # No invented prices: nothing is priced until MedCart Tech sets it.
    assert all(p["price_monthly"] is None for p in catalogue.values())


def test_basic_plan_is_enforced_on_the_api(api, shop):
    h = shop["headers"]
    assert api.client.get("/assistant/status", headers=h).status_code == 403
    assert api.client.get("/transfers", headers=h).status_code == 403
    assert api.client.post("/locations", headers=h, json={"name": "Second Branch", "location_type": "PHARMACY"}
                           ).status_code == 403  # max_locations = 1


def test_platform_edits_plan_catalogue_with_step_up(api, shop):
    plans = api.get("/platform/plans").json()
    basic = next(p for p in plans["plans"] if p["code"] == "BASIC")
    body = {"label": basic["label"], "description": basic["description"], "limits": {**basic["limits"], "max_medicines": 1},
            "features": basic["features"], "price_monthly": 150, "price_annual": 1500, "currency": "GHS",
            "is_active": True, "sort_order": 1}
    assert api.put("/platform/plans/BASIC", {**body, "features": ["teleportation"]}).status_code == 400
    assert api.put("/platform/plans/BASIC", {**body, "price_monthly": 1, "currency": None}).status_code == 400
    saved = api.put("/platform/plans/BASIC", body)
    assert saved.status_code == 200, saved.text
    h = shop["headers"]
    medicine = {"name": "Limit Tabs", "strength": "1 mg", "dosage_form": "Tablet", "reorder_level": 1}
    assert api.client.post("/medicines", headers=h, json=medicine).status_code == 200
    blocked = api.client.post("/medicines", headers=h, json={**medicine, "name": "Limit Tabs 2"})
    assert blocked.status_code == 403 and "at most 1" in blocked.json()["detail"]
    audit = api.get("/platform/audit", params={"action": "PLAN_SAVED"}).json()["items"]
    assert audit and audit[0]["details"]["after"]["price_monthly"] == 150
    # A per-organization override lifts the limit for this organization only.
    assert api.put(f"/platform/organizations/{shop['id']}", {"plan": "BASIC", "status": "TRIAL",
                                                             "limits": {"max_medicines": 50}}).status_code == 200
    assert api.client.post("/medicines", headers=h, json={**medicine, "name": "Limit Tabs 2"}).status_code == 200
    # Restore the catalogue for the other tests.
    api.put("/platform/plans/BASIC", {**body, "limits": basic["limits"]})


def test_only_owner_manages_billing(api, shop):
    assert api.get("/billing", role="ADMINISTRATOR").status_code == 403
    overview = api.client.get("/billing", headers=shop["headers"])
    assert overview.status_code == 200
    assert overview.json()["messaging"]["credit_balance"] == 0


def test_manual_checkout_activates_only_after_platform_confirmation(api, shop):
    h = shop["headers"]
    refused = api.client.post("/billing/checkout", headers=h,
                              json={"purpose": "SUBSCRIPTION", "plan_code": "ENTERPRISE", "billing_cycle": "MONTHLY"})
    assert refused.status_code == 400 and "not sold online" in refused.json()["detail"]
    checkout = api.client.post("/billing/checkout", headers=h,
                               json={"purpose": "SUBSCRIPTION", "plan_code": "BASIC", "billing_cycle": "ANNUAL"})
    assert checkout.status_code == 201, checkout.text
    payment = checkout.json()
    assert payment["amount"] == 1500 and payment["instructions"] and payment["checkout_url"] is None
    me = api.client.get("/organization", headers=h).json()
    assert me["status"] == "TRIAL"  # nothing changes before confirmation
    # The organization cannot confirm its own payment.
    assert api.client.post(f"/platform/payments/{payment['payment_id']}/confirm", headers=h,
                           json={"note": "paid"}).status_code == 403
    confirmed = api.post(f"/platform/payments/{payment['payment_id']}/confirm", {"note": "Bank transfer received"})
    assert confirmed.status_code == 200, confirmed.text
    again = api.post(f"/platform/payments/{payment['payment_id']}/confirm", {"note": "Bank transfer received"})
    assert again.json().get("already_applied") is True
    me = api.client.get("/organization", headers=h).json()
    assert me["status"] == "ACTIVE" and me["current_period_end"]
    with psycopg.connect(TEST_URL) as conn:
        assert conn.execute("SELECT COUNT(*) FROM subscriptions WHERE organization_id = %s",
                            (shop["id"],)).fetchone()[0] == 1


def test_paystack_webhook_requires_valid_signature(api, shop, monkeypatch):
    from dataclasses import replace

    from backend.config import settings
    from backend.routers import billing as billing_router
    from backend.services import billing as billing_service
    patched = replace(settings, payment_provider="paystack", paystack_secret_key="sk_test_signing")
    monkeypatch.setattr(billing_router, "settings", patched)
    monkeypatch.setattr(billing_service, "settings", patched)
    with psycopg.connect(TEST_URL) as conn:
        conn.execute("INSERT INTO messaging_packages (code, label, credits, price, currency, is_active) "
                     "VALUES ('SMS100', '100 messages', 100, 20, 'GHS', true)")
        conn.execute(
            "INSERT INTO payments (organization_id, purpose, package_code, amount, currency, provider, reference) "
            "VALUES (%s, 'MESSAGING_CREDITS', 'SMS100', 20, 'GHS', 'paystack', 'PS-TEST-REF-1')", (shop["id"],))
        conn.commit()

    def send(payload, secret="sk_test_signing"):
        raw = json.dumps(payload).encode()
        signature = hmac.new(secret.encode(), raw, hashlib.sha512).hexdigest()
        return api.client.post("/billing/webhooks/paystack", content=raw,
                               headers={"x-paystack-signature": signature, "content-type": "application/json"})

    good = {"event": "charge.success", "data": {"reference": "PS-TEST-REF-1", "amount": 2000,
                                                  "currency": "GHS", "status": "success"}}
    assert send(good, secret="forged").status_code == 401
    unsigned = api.client.post("/billing/webhooks/paystack", json=good)
    assert unsigned.status_code == 401
    wrong_amount = {**good, "data": {**good["data"], "amount": 100}}
    assert send(wrong_amount).status_code == 400
    with psycopg.connect(TEST_URL) as conn:
        conn.execute("UPDATE payments SET status = 'PENDING' WHERE reference = 'PS-TEST-REF-1'")
        conn.commit()
    assert send(good).status_code == 200
    assert send(good).status_code == 200  # replayed webhook: applied once
    credits = api.client.get("/billing/credits", headers=shop["headers"]).json()
    assert credits["balance"] == 100 and len(credits["entries"]) == 1
    assert any(r["action"] == "PAYMENT_CONFIRMED_BY_PROVIDER"
               for r in api.get("/platform/audit", params={"organization_id": shop["id"]}).json()["items"])


def test_online_payment_cannot_be_confirmed_manually(api, shop):
    with psycopg.connect(TEST_URL) as conn:
        pid = conn.execute("SELECT id FROM payments WHERE reference = 'PS-TEST-REF-1'").fetchone()[0]
    assert api.post(f"/platform/payments/{pid}/confirm", {"note": "trying"}).status_code == 400


def test_platform_credit_grant_and_usage(api, shop):
    granted = api.post(f"/platform/organizations/{shop['id']}/credits",
                       {"change": 50, "reason": "GRANT", "note": "Pilot bonus"})
    assert granted.status_code == 200 and granted.json()["balance"] == 150
    usage = api.get("/platform/usage").json()
    row = next(r for r in usage if r["organization_id"] == shop["id"])
    assert row["credit_balance"] == 150 and row["usage"]["medicines"] >= 1
    # Organization 1 does not see organization 2's ledger.
    with psycopg.connect(TEST_URL) as conn:
        conn.execute("SELECT set_config('app.organization_id', '1', false)")
        assert conn.execute("SELECT COUNT(*) FROM messaging_credit_ledger").fetchone()[0] == 0


def test_trial_end_marks_subscription_status(api):
    from backend.services import billing
    with psycopg.connect(TEST_URL) as conn:
        oid = conn.execute("INSERT INTO organizations (name, plan, status, trial_ends_at) VALUES "
                           "('Expired Trial', 'BASIC', 'TRIAL', CURRENT_TIMESTAMP - interval '1 day') RETURNING id"
                           ).fetchone()[0]
        conn.commit()
    from psycopg.rows import dict_row
    with psycopg.connect(TEST_URL, row_factory=dict_row) as conn:
        assert billing.expire_due(conn) >= 1
        conn.commit()
        row = conn.execute("SELECT status, subscription_status FROM organizations WHERE id = %s", (oid,)).fetchone()
    assert row == {"status": "TRIAL", "subscription_status": "TRIAL_ENDED"}  # flagged, not suspended
