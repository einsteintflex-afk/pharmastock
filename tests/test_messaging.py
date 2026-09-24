# Messaging gateway: customer receipts on WhatsApp / SMS / e-mail with
# consent, messaging modes (disabled, own provider, MedCart credits),
# credits, WhatsApp Cloud API template payloads, signed webhooks (delivery
# status, STOP opt-out), SSRF protection, secrets encrypted and masked.

import hashlib
import hmac
import json
from datetime import date, timedelta

import psycopg
import pytest

from backend.services import delivery
from tests.conftest import TEST_URL, org_connection

APP_SECRET = "org-app-secret-123"


class FakeHttp:
    def __init__(self):
        self.calls = []
        self.counter = 0

    def __call__(self, url, payload, headers):
        self.calls.append({"url": url, "payload": payload, "headers": headers})
        self.counter += 1
        if "graph.facebook.com" in url:
            return 200, {"messages": [{"id": f"wamid.TEST{self.counter}"}]}
        return 200, {"id": f"sms-{self.counter}"}


class FakeEmail:
    def __init__(self):
        self.sent = []

    def send(self, recipient, subject, body, attachment):
        self.sent.append((recipient, subject, body))


@pytest.fixture
def http(monkeypatch):
    fake = FakeHttp()
    monkeypatch.setattr(delivery, "http_post_json", fake)
    monkeypatch.setattr(delivery, "check_public_host", lambda host: None)
    email = FakeEmail()
    monkeypatch.setattr(delivery, "email_sender", email)
    fake.email = email
    return fake


def _process():
    from psycopg.rows import dict_row
    with org_connection(1, row_factory=dict_row) as conn:
        return delivery.process_outbox(conn)


@pytest.fixture(scope="module")
def med(api):
    medicine = api.post("/medicines", {"name": "Message Tabs", "strength": "5 mg", "dosage_form": "Tablet",
                                       "reorder_level": 1, "selling_price": 3}).json()
    api.post("/batches", {"medicine_id": medicine["id"], "batch_number": "MSG-1", "quantity": 500,
                          "expiry_date": (date.today() + timedelta(days=400)).isoformat()})
    api.put("/settings", {"comms.whatsapp_receipts": True, "comms.sms_receipts": True, "comms.email_receipts": True})
    return medicine["id"]


def _sale(api, med, phone="+233241112222", consent=None, email=None):
    body = {"dispense_type": "OTC", "payment_method": "CASH", "patient_name": "Kofi Owusu", "patient_phone": phone,
            "items": [{"medicine_id": med, "quantity": 2}], "consent": consent or {}}
    if email:
        body["customer_email"] = email
    response = api.post("/dispensations", body)
    assert response.status_code == 201, response.text
    return response.json()


def test_no_messages_while_messaging_is_disabled(api, med):
    assert api.get("/messaging").json()["mode"] == "DISABLED"
    sale = _sale(api, med, consent={"whatsapp": True})
    assert sale["messages_queued"] == []


def test_no_consent_no_message(api, med):
    assert api.put("/messaging/mode", {"mode": "PLATFORM_CREDITS"}).status_code == 200
    sale = _sale(api, med, consent={})
    assert sale["messages_queued"] == []


def test_platform_credits_mode_charges_and_sends_whatsapp_template(api, med, http, monkeypatch, db):
    from dataclasses import replace

    from backend.config import settings
    monkeypatch.setattr(delivery, "settings", replace(settings, whatsapp_phone_number_id="PLATFORM-NUM",
                                                      whatsapp_access_token="platform-token"))
    sale = _sale(api, med, consent={"whatsapp": True})
    assert sale["messages_queued"] == ["WHATSAPP"]
    # No credits yet: skipped, nothing charged, nothing sent.
    assert _process()["skipped"] >= 1
    row = db.execute("SELECT status, last_error, credits_charged FROM notification_deliveries "
                     "WHERE dispensation_id = %s", (sale["id"],)).fetchone()
    assert row["status"] == "SKIPPED" and "credits" in row["last_error"] and row["credits_charged"] == 0
    assert http.calls == []

    api.post("/platform/organizations/1/credits", {"change": 2, "reason": "GRANT", "note": "test credits"})
    sale = _sale(api, med, consent={"whatsapp": True})
    assert _process()["sent"] == 1
    call = http.calls[-1]
    assert call["url"].endswith("/PLATFORM-NUM/messages") and call["headers"]["Authorization"] == "Bearer platform-token"
    template = call["payload"]["template"]
    assert template["name"] == "pharmastock_receipt" and call["payload"]["to"] == "233241112222"
    params = [p["text"] for p in template["components"][0]["parameters"]]
    assert params[0] == "Kofi" and params[3].endswith("/r/" + sale["receipt_token"])
    sent = db.execute("SELECT provider, provider_message_id, credits_charged FROM notification_deliveries "
                      "WHERE dispensation_id = %s", (sale["id"],)).fetchone()
    assert sent == {"provider": "whatsapp_cloud", "provider_message_id": "wamid.TEST1", "credits_charged": 1}
    assert api.get("/billing/credits", role="OWNER").json()["balance"] == 1


def _signed(body: dict, secret: str):
    raw = json.dumps(body).encode()
    return raw, "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def test_whatsapp_status_webhook_is_verified(api, monkeypatch):
    from dataclasses import replace

    from backend.config import settings
    from backend.services import messaging
    monkeypatch.setattr(messaging, "settings", replace(settings, whatsapp_phone_number_id="PLATFORM-NUM",
                                                       whatsapp_app_secret="platform-secret"))
    payload = {"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "PLATFORM-NUM"},
                                                  "statuses": [{"id": "wamid.TEST1", "status": "delivered"}]}}]}]}
    raw, signature = _signed(payload, "wrong-secret")
    assert api.client.post("/messaging/webhooks/whatsapp", content=raw,
                           headers={"x-hub-signature-256": signature}).status_code == 401
    assert api.client.post("/messaging/webhooks/whatsapp", content=raw).status_code == 401
    raw, signature = _signed(payload, "platform-secret")
    ok = api.client.post("/messaging/webhooks/whatsapp", content=raw, headers={"x-hub-signature-256": signature})
    assert ok.status_code == 200 and ok.json()["statuses"] == 1
    with org_connection(1) as conn:
        assert conn.execute("SELECT delivery_status FROM notification_deliveries "
                            "WHERE provider_message_id = 'wamid.TEST1'").fetchone()[0] == "DELIVERED"
    # An older status never overwrites a newer one.
    raw, signature = _signed({"entry": [{"changes": [{"value": {"metadata": {"phone_number_id": "PLATFORM-NUM"},
                              "statuses": [{"id": "wamid.TEST1", "status": "sent"}]}}]}]}, "platform-secret")
    api.client.post("/messaging/webhooks/whatsapp", content=raw, headers={"x-hub-signature-256": signature})
    with org_connection(1) as conn:
        assert conn.execute("SELECT delivery_status FROM notification_deliveries "
                            "WHERE provider_message_id = 'wamid.TEST1'").fetchone()[0] == "DELIVERED"


def test_whatsapp_verify_handshake(api, monkeypatch):
    from dataclasses import replace

    from backend.config import settings
    from backend.routers import messaging as router
    monkeypatch.setattr(router, "settings", replace(settings, whatsapp_verify_token="verify-me"))
    ok = api.client.get("/messaging/webhooks/whatsapp",
                        params={"hub.mode": "subscribe", "hub.verify_token": "verify-me", "hub.challenge": "42"})
    assert ok.status_code == 200 and ok.text == "42"
    assert api.client.get("/messaging/webhooks/whatsapp",
                          params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"}
                          ).status_code == 403


def test_own_provider_secrets_encrypted_masked_and_used(api, med, http, db):
    assert api.put("/messaging/mode", {"mode": "OWN_PROVIDER"}).status_code == 200
    config = {"phone_number_id": "ORG1-NUM", "access_token": "org-access-token-9999", "app_secret": APP_SECRET,
              "receipt_template": "org_receipt", "language": "en_GB"}
    assert api.put("/messaging/providers/WHATSAPP", {"config": {**config, "app_secret": ""}}).status_code == 400
    saved = api.put("/messaging/providers/WHATSAPP", {"config": config})
    assert saved.status_code == 200
    assert saved.json()["config"]["access_token"] == "••••9999"
    with org_connection(1) as conn:
        stored = conn.execute("SELECT config_encrypted FROM messaging_providers WHERE channel = 'WHATSAPP'").fetchone()[0]
    assert "org-access-token" not in stored and APP_SECRET not in stored
    # Sending back the masked value keeps the stored secret.
    again = api.put("/messaging/providers/WHATSAPP", {"config": {**config, "access_token": "••••9999"}})
    assert again.status_code == 200
    audit = api.get("/audit-log", params={"action": "MESSAGING_PROVIDER_SAVED"}).json()
    assert "org-access-token" not in json.dumps(audit)
    assert api.get("/messaging", role="PHARMACIST").status_code == 403

    sale = _sale(api, med, consent={"whatsapp": True})
    assert _process()["sent"] == 1
    call = http.calls[-1]
    assert "/ORG1-NUM/messages" in call["url"] and call["headers"]["Authorization"] == "Bearer org-access-token-9999"
    assert call["payload"]["template"]["name"] == "org_receipt"
    assert call["payload"]["template"]["language"]["code"] == "en_GB"
    charged = db.execute("SELECT credits_charged FROM notification_deliveries WHERE dispensation_id = %s",
                         (sale["id"],)).fetchone()["credits_charged"]
    assert charged == 0  # own provider: no MedCart credits


def test_stop_reply_opts_the_customer_out(api, med, http):
    payload = {"entry": [{"changes": [{"value": {
        "metadata": {"phone_number_id": "ORG1-NUM"},
        "messages": [{"from": "233241112222", "type": "text", "text": {"body": "stop"}}]}}]}]}
    raw, signature = _signed(payload, APP_SECRET)
    result = api.client.post("/messaging/webhooks/whatsapp", content=raw, headers={"x-hub-signature-256": signature})
    assert result.status_code == 200 and result.json()["opt_outs"] == 1
    consents = api.get("/messaging/consents").json()
    assert any(c["phone"] == "+233241112222" and c["status"] == "OPTED_OUT" for c in consents)
    # A message queued before the opt-out is not sent.
    sale = _sale(api, med, phone="0241112222", consent={})
    with org_connection(1) as conn:
        conn.execute("INSERT INTO notification_deliveries (channel, recipient, body, message_type, dispensation_id) "
                     "VALUES ('WHATSAPP', '+233241112222', 'x', 'RECEIPT', %s)", (sale["id"],))
        conn.commit()
    before = len(http.calls)
    _process()
    assert len(http.calls) == before


def test_email_receipt_requires_consent_and_address(api, med, http):
    api.put("/messaging/mode", {"mode": "PLATFORM_CREDITS"})  # e-mail through MedCart Tech costs no credits
    sale = _sale(api, med, phone="+233209990000", consent={"email": True}, email="kofi@example.com")
    assert "EMAIL" in sale["messages_queued"]
    _process()
    recipient, subject, body = http.email.sent[-1]
    assert recipient == "kofi@example.com" and "receipt" in subject.lower() and sale["receipt_token"] in body


def test_own_sms_gateway_must_be_public_https(api, monkeypatch):
    assert api.put("/messaging/providers/SMS", {"config": {"url": "http://10.0.0.5/send"}}).status_code == 400
    assert api.put("/messaging/providers/SMS", {"config": {"url": "https://127.0.0.1/send"}}).status_code == 200
    with pytest.raises(delivery.NotConfigured):
        delivery.check_public_host("127.0.0.1")
    with pytest.raises(delivery.NotConfigured):
        delivery.check_public_host("169.254.169.254")


def test_whatsapp_number_cannot_be_claimed_twice(api):
    shop = api.post("/platform/organizations", {"name": "Number Thief", "plan": "PROFESSIONAL", "status": "ACTIVE",
                                                "admin_username": "thiefowner", "admin_full_name": "T",
                                                "admin_password": "Thief-owner-pass-1"}).json()
    token = api.client.post("/auth/login", json={"username": "thiefowner", "password": "Thief-owner-pass-1"}).json()["token"]
    api.client.cookies.clear()
    h = {"Authorization": f"Bearer {token}"}
    api.client.post("/auth/change-password", headers=h, json={"current_password": "Thief-owner-pass-1",
                                                              "new_password": "Thief-owner-new-2"})
    stolen = api.client.put("/messaging/providers/WHATSAPP", headers=h, json={"config": {
        "phone_number_id": "ORG1-NUM", "access_token": "x" * 10, "app_secret": "y" * 10, "receipt_template": "t"}})
    assert stolen.status_code == 409
    assert shop["organization"]["id"] != 1


def test_basic_plan_cannot_use_whatsapp(api):
    with psycopg.connect(TEST_URL) as conn:
        feats = conn.execute("SELECT features FROM plans WHERE code = 'BASIC'").fetchone()[0]
    assert "whatsapp" not in feats and "sms" not in feats and "email_receipts" in feats
