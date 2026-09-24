# ============================================================
# CUSTOMER MESSAGING: RECEIPTS, PROVIDERS, CONSENT, WEBHOOKS
# ============================================================
# Queues customer messages after a sale (sent by the delivery worker, see
# services/delivery.py), stores organizations' own provider credentials
# (encrypted), and applies the WhatsApp Business Platform webhooks
# (delivery status, STOP opt-outs) after verifying their signature.

import hashlib
import hmac
import json
import logging

import psycopg
from fastapi import HTTPException

from .. import crypto
from ..config import settings
from ..database import set_organization
from . import app_settings, branding, delivery, plans

logger = logging.getLogger("pharmastock.messaging")

MODES = ("DISABLED", "PLATFORM_CREDITS", "OWN_PROVIDER")
OPT_OUT_WORDS = {"STOP", "UNSUBSCRIBE", "STOP ALL", "OPT OUT", "OPTOUT"}

# Fields per channel for an organization's own provider; secrets are never returned.
PROVIDER_FIELDS = {
    "WHATSAPP": {"provider": "whatsapp_cloud",
                 "fields": ["phone_number_id", "access_token", "app_secret", "receipt_template",
                            "thank_you_template", "language"],
                 "required": ["phone_number_id", "access_token", "app_secret", "receipt_template"],
                 "secret": ["access_token", "app_secret"]},
    "SMS": {"provider": "sms_webhook", "fields": ["url", "token"], "required": ["url"], "secret": ["token"]},
    "EMAIL": {"provider": "smtp", "fields": ["host", "port", "security", "username", "password", "from"],
              "required": ["host", "from"], "secret": ["password"]},
}


def _money(value, currency: str) -> str:
    return f"{currency}{float(value or 0):,.2f}"


def queue_receipt_messages(conn: psycopg.Connection, organization_id: int, sale: dict, receipt_url: str) -> list[str]:
    """After a sale: queue the receipt / thank-you on each channel the
    organization enabled AND the customer consented to. Returns the channels."""
    org = plans.organization(conn, organization_id)
    if org["messaging_mode"] == "DISABLED":
        return []
    values = app_settings.get_all(conn)
    features = set(plans.effective(conn, org)["features"])
    brand = branding.profile(conn, with_logo=False)
    name = (sale.get("patient_name") or "Customer").split(" ")[0][:40]
    total = _money(sale["total_amount"], brand["currency"])
    thanks = values.get("comms.thank_you_text") or ""
    queued = []
    phone = sale.get("patient_phone")
    common = {"dispensation_id": sale["id"], "consent_status": "CONSENTED"}
    if phone and sale.get("consent_whatsapp") and "whatsapp" in features:
        if values.get("comms.whatsapp_receipts"):
            delivery.enqueue(conn, channel="WHATSAPP", recipient=phone, message_type="RECEIPT",
                             body=f"Receipt {sale['dispensation_number']} {total} {receipt_url}",
                             template_params=[name, brand["name"], total, receipt_url], **common)
            queued.append("WHATSAPP")
        if values.get("comms.whatsapp_thank_you"):
            delivery.enqueue(conn, channel="WHATSAPP", recipient=phone, message_type="THANK_YOU",
                             body=thanks, template_params=[name, brand["name"], thanks], **common)
            queued.append("WHATSAPP_THANK_YOU")
    if phone and sale.get("consent_sms") and "sms" in features and values.get("comms.sms_receipts"):
        text = f"{brand['name']}: thank you {name}. Receipt {sale['dispensation_number']}, total {total}. {receipt_url}"
        delivery.enqueue(conn, channel="SMS", recipient=phone, message_type="RECEIPT", body=text[:480], **common)
        queued.append("SMS")
    if sale.get("customer_email") and sale.get("consent_email") and "email_receipts" in features \
            and values.get("comms.email_receipts"):
        body = (f"Dear {name},\n\n{thanks}\n\nYour receipt {sale['dispensation_number']} from {brand['name']}: "
                f"total {total}.\nView it online: {receipt_url}\n\n{brand['name']}\n"
                + "\n".join(x for x in (brand["address"], brand["phone"]) if x)
                + (f"\n\n{brand['powered_by']}" if brand.get("powered_by") else ""))
        delivery.enqueue(conn, channel="EMAIL", recipient=sale["customer_email"], message_type="RECEIPT",
                         subject=f"Your receipt from {brand['name']}", body=body, **common)
        queued.append("EMAIL")
    return queued


# ------------------------------------------------------------
# Organization's own providers
# ------------------------------------------------------------

def provider_summary(conn: psycopg.Connection) -> dict:
    """Configured providers with secrets masked."""
    result = {}
    for row in conn.execute("SELECT channel, provider, config_encrypted, is_active, updated_at FROM messaging_providers"
                            ).fetchall():
        config = json.loads(crypto.decrypt(row["config_encrypted"]))
        secret = PROVIDER_FIELDS[row["channel"]]["secret"]
        result[row["channel"]] = {
            "provider": row["provider"], "is_active": row["is_active"], "updated_at": row["updated_at"],
            "config": {k: ("••••" + str(v)[-4:] if k in secret and v else v) for k, v in config.items()},
        }
    return result


def save_provider(conn: psycopg.Connection, organization_id: int, user_id: int, channel: str, config: dict) -> None:
    spec = PROVIDER_FIELDS[channel]
    existing = conn.execute("SELECT config_encrypted FROM messaging_providers WHERE channel = %s", (channel,)).fetchone()
    old = json.loads(crypto.decrypt(existing["config_encrypted"])) if existing else {}
    clean = {}
    for field in spec["fields"]:
        value = config.get(field)
        if isinstance(value, str):
            value = value.strip()
        # A masked secret sent back unchanged keeps the stored value.
        if field in spec["secret"] and (value in (None, "") or str(value).startswith("••••")):
            value = old.get(field)
        if value not in (None, ""):
            clean[field] = value
    missing = [f for f in spec["required"] if not clean.get(f)]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing: {', '.join(missing)}")
    if channel == "SMS" and not str(clean["url"]).startswith("https://"):
        raise HTTPException(status_code=400, detail="The SMS gateway URL must start with https://")
    if channel == "EMAIL":
        if clean.get("security", "starttls") not in ("starttls", "ssl"):
            raise HTTPException(status_code=400, detail="security must be starttls or ssl")
        try:
            clean["port"] = int(clean.get("port") or 587)
        except ValueError:
            raise HTTPException(status_code=400, detail="port must be a number")
    if channel == "WHATSAPP":
        taken = conn.execute("SELECT organization_id FROM whatsapp_numbers WHERE phone_number_id = %s",
                             (str(clean["phone_number_id"]),)).fetchone()
        if taken and taken["organization_id"] != organization_id:
            raise HTTPException(status_code=409, detail="This WhatsApp number is registered to another organization")
        if str(clean["phone_number_id"]) == (settings.whatsapp_phone_number_id or ""):
            raise HTTPException(status_code=409, detail="This is MedCart Tech's platform number")
        conn.execute("DELETE FROM whatsapp_numbers WHERE organization_id = %s", (organization_id,))
        conn.execute("INSERT INTO whatsapp_numbers (phone_number_id, organization_id) VALUES (%s, %s)",
                     (str(clean["phone_number_id"]), organization_id))
    conn.execute(
        """
        INSERT INTO messaging_providers (channel, provider, config_encrypted, updated_by)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (organization_id, channel) DO UPDATE SET provider = EXCLUDED.provider,
            config_encrypted = EXCLUDED.config_encrypted, is_active = true, updated_by = EXCLUDED.updated_by,
            updated_at = CURRENT_TIMESTAMP
        """,
        (channel, spec["provider"], crypto.encrypt(json.dumps(clean)), user_id),
    )


def delete_provider(conn: psycopg.Connection, organization_id: int, channel: str) -> bool:
    if channel == "WHATSAPP":
        conn.execute("DELETE FROM whatsapp_numbers WHERE organization_id = %s", (organization_id,))
    return conn.execute("DELETE FROM messaging_providers WHERE channel = %s RETURNING id", (channel,)).fetchone() is not None


# ------------------------------------------------------------
# WhatsApp webhook
# ------------------------------------------------------------

def _app_secret_for(conn, phone_number_id: str) -> tuple[int | None, str | None]:
    """(organization id or None for the platform number, app secret)."""
    if phone_number_id and phone_number_id == settings.whatsapp_phone_number_id:
        return None, settings.whatsapp_app_secret
    route = conn.execute("SELECT organization_id FROM whatsapp_numbers WHERE phone_number_id = %s",
                         (phone_number_id,)).fetchone()
    if route is None:
        return None, None
    set_organization(conn, route["organization_id"])
    row = conn.execute("SELECT config_encrypted FROM messaging_providers WHERE channel = 'WHATSAPP'").fetchone()
    set_organization(conn, None)
    if row is None:
        return route["organization_id"], None
    return route["organization_id"], json.loads(crypto.decrypt(row["config_encrypted"])).get("app_secret")


def verify_signature(secret: str | None, body: bytes, header: str | None) -> bool:
    if not secret or not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[7:], expected)


STATUS_MAP = {"sent": "SENT", "delivered": "DELIVERED", "read": "READ", "failed": "FAILED"}
_RANK = {"SENT": 1, "DELIVERED": 2, "READ": 3, "FAILED": 4}


def apply_whatsapp_webhook(conn: psycopg.Connection, body: bytes, signature: str | None) -> dict:
    """Verify and apply one webhook call. Raises 401 on a bad signature."""
    try:
        payload = json.loads(body)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    changes = [change.get("value") or {} for entry in payload.get("entry") or [] for change in entry.get("changes") or []]
    if not changes:
        return {"statuses": 0, "opt_outs": 0}
    # Every change in one call comes from the same business number.
    phone_number_id = str((changes[0].get("metadata") or {}).get("phone_number_id") or "")
    organization_id, secret = _app_secret_for(conn, phone_number_id)
    if not verify_signature(secret, body, signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    applied = opt_outs = 0
    for value in changes:
        for status in value.get("statuses") or []:
            new = STATUS_MAP.get(status.get("status"))
            route = conn.execute(
                "SELECT organization_id, delivery_id FROM provider_message_routes "
                "WHERE provider = ANY(%s) AND provider_message_id = %s",
                (["whatsapp_cloud"], str(status.get("id"))),
            ).fetchone()
            if not new or route is None:
                continue
            set_organization(conn, route["organization_id"])
            row = conn.execute("SELECT id, delivery_status, credits_charged FROM notification_deliveries "
                               "WHERE id = %s FOR UPDATE", (route["delivery_id"],)).fetchone()
            if row and _RANK.get(new, 0) > _RANK.get(row["delivery_status"] or "", 0):
                error = "; ".join(e.get("title") or e.get("message") or "" for e in status.get("errors") or [])
                conn.execute(
                    "UPDATE notification_deliveries SET delivery_status = %s, delivery_status_at = CURRENT_TIMESTAMP, "
                    "last_error = COALESCE(NULLIF(%s, ''), last_error) WHERE id = %s", (new, error, row["id"]))
                if new == "FAILED" and row["credits_charged"]:
                    conn.execute("INSERT INTO messaging_credit_ledger (change, reason, delivery_id, note) "
                                 "VALUES (%s, 'REFUND', %s, 'WhatsApp reported failure')",
                                 (row["credits_charged"], row["id"]))
                    conn.execute("UPDATE notification_deliveries SET credits_charged = 0 WHERE id = %s", (row["id"],))
                applied += 1
            set_organization(conn, None)
        for message in value.get("messages") or []:
            text = ((message.get("text") or {}).get("body") or "").strip().upper()
            phone = "+" + str(message.get("from") or "").lstrip("+")
            if text in OPT_OUT_WORDS and len(phone) > 4:
                if organization_id is None:
                    conn.execute("INSERT INTO platform_opt_outs (phone, channel) VALUES (%s, 'WHATSAPP') "
                                 "ON CONFLICT DO NOTHING", (phone,))
                else:
                    set_organization(conn, organization_id)
                    # Numbers are stored as typed at the counter (local or international):
                    # match on the last 9 digits.
                    updated = conn.execute(
                        """
                        UPDATE message_consents SET status = 'OPTED_OUT', source = 'WHATSAPP_STOP',
                               recorded_at = CURRENT_TIMESTAMP
                        WHERE channel = 'WHATSAPP' AND right(regexp_replace(phone, '\\D', '', 'g'), 9) = right(%s, 9)
                        RETURNING id
                        """,
                        (phone.lstrip("+"),),
                    ).fetchall()
                    if not updated:
                        conn.execute("INSERT INTO message_consents (phone, channel, status, source) "
                                     "VALUES (%s, 'WHATSAPP', 'OPTED_OUT', 'WHATSAPP_STOP') "
                                     "ON CONFLICT DO NOTHING", (phone,))
                    set_organization(conn, None)
                opt_outs += 1
    return {"statuses": applied, "opt_outs": opt_outs}
