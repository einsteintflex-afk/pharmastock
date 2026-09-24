# ============================================================
# MESSAGING GATEWAY: E-MAIL, SMS AND WHATSAPP OUTBOX
# ============================================================
# Messages are queued in notification_deliveries inside the transaction that
# raised them and sent later by process_outbox() (background worker), with
# exponential back-off: a provider outage never blocks stock work and
# messages are not lost. Channels that are not configured mark messages
# SKIPPED with the reason, so administrators can see why nothing arrived.
#
# Two kinds of messages:
#   * staff messages (alerts, reports, password resets, tests) use the
#     deployment's own e-mail / SMS configuration, as before;
#   * customer messages (receipts, thank-you) follow the organization's
#     messaging mode:
#       DISABLED          nothing is sent
#       OWN_PROVIDER      the organization's own WhatsApp Business / SMS / SMTP
#                         account (credentials encrypted at rest)
#       PLATFORM_CREDITS  MedCart Tech's accounts; each WhatsApp / SMS message
#                         costs one messaging credit (no credit = not sent)
#     and are sent only with the customer's recorded consent, re-checked at
#     sending time (an opt-out after the sale is honoured).
#
# WhatsApp uses only the official WhatsApp Business Platform (Cloud API) with
# pre-approved message templates.

import ipaddress
import json
import logging
import smtplib
import socket
import ssl
import urllib.error
import urllib.request
from email.message import EmailMessage
from email.utils import make_msgid
from urllib.parse import urlparse

import psycopg

from ..config import settings

logger = logging.getLogger("pharmastock.delivery")

MAX_ATTEMPTS = 5
SEVERITY_RANK = {"INFO": 0, "WARNING": 1, "CRITICAL": 2}


class DeliveryError(Exception):
    pass


class NotConfigured(DeliveryError):
    pass


# ------------------------------------------------------------
# Senders
# ------------------------------------------------------------

CUSTOMER_TYPES = ("RECEIPT", "THANK_YOU")


def http_post_json(url: str, payload: dict, headers: dict) -> tuple[int, dict]:
    """POST JSON, return (status, JSON body). Replaced in tests."""
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 (checked URL)
            raw = response.read()
            return response.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as error:
        try:
            return error.code, json.loads(error.read() or b"{}")
        except ValueError:
            return error.code, {}
    except OSError as error:
        raise DeliveryError(f"Provider unreachable: {error}") from error


def check_public_host(host: str) -> None:
    """Organizations' own provider hosts must be public internet addresses:
    the server must not be used to reach its own network (SSRF)."""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError as error:
        raise DeliveryError(f"Cannot resolve {host}") from error
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%")[0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast \
                or ip.is_unspecified:
            raise NotConfigured(f"{host} is not a public address")


class EmailSender:
    """SMTP. config=None: the deployment's SMTP_* settings; otherwise an
    organization's own server (public host only)."""

    def __init__(self, config: dict | None = None):
        self.config = config

    def send(self, recipient: str, subject: str, body: str, attachment: tuple[str, str, bytes] | None) -> None:
        c = self.config or {"host": settings.smtp_host, "port": settings.smtp_port, "security": settings.smtp_security,
                            "username": settings.smtp_username, "password": settings.smtp_password,
                            "from": settings.smtp_from}
        if not c.get("host") or not c.get("from"):
            raise NotConfigured("E-mail is not configured (SMTP_HOST / SMTP_FROM)")
        if self.config:
            check_public_host(c["host"])
        message = EmailMessage()
        message["From"] = c["from"]
        message["To"] = recipient
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain="pharmastock")
        message.set_content(body)
        if attachment:
            name, content_type, content = attachment
            maintype, _, subtype = content_type.partition("/")
            message.add_attachment(content, maintype=maintype, subtype=subtype or "octet-stream", filename=name)

        context = ssl.create_default_context()
        port = int(c.get("port") or 587)
        if c.get("security") == "ssl":
            client = smtplib.SMTP_SSL(c["host"], port, timeout=30, context=context)
        else:
            client = smtplib.SMTP(c["host"], port, timeout=30)
        try:
            if c.get("security", "starttls") == "starttls":
                client.starttls(context=context)
            if c.get("username"):
                client.login(c["username"], c.get("password") or "")
            client.send_message(message)
        except (smtplib.SMTPException, OSError) as error:
            raise DeliveryError(f"SMTP error: {error}") from error
        finally:
            try:
                client.quit()
            except (smtplib.SMTPException, OSError):
                pass


class SmsSender:
    """SMS gateway behind a JSON webhook ({"to", "message"}). config=None:
    the deployment's SMS_* settings; otherwise an organization's own gateway
    (HTTPS, public host only). Returns the gateway's message id if it gives one."""

    def __init__(self, config: dict | None = None):
        self.config = config

    def send(self, recipient: str, text: str) -> str | None:
        if self.config:
            url, token = self.config.get("url"), self.config.get("token")
            parsed = urlparse(url or "")
            if parsed.scheme != "https" or not parsed.hostname:
                raise NotConfigured("The SMS gateway URL must be an https:// address")
            check_public_host(parsed.hostname)
        else:
            provider = settings.sms_provider
            if provider == "log":
                logger.info("SMS to %s: %s", recipient, text)
                return None
            if provider != "webhook" or not settings.sms_webhook_url:
                raise NotConfigured("SMS is not configured (SMS_PROVIDER / SMS_WEBHOOK_URL)")
            url, token = settings.sms_webhook_url, settings.sms_webhook_token
        status, body = http_post_json(url, {"to": recipient, "message": text},
                                      {"Authorization": f"Bearer {token}"} if token else {})
        if status >= 300:
            raise DeliveryError(f"SMS gateway returned HTTP {status}")
        return str(body.get("id") or body.get("message_id") or "") or None


class WhatsAppSender:
    """WhatsApp Business Platform (Cloud API), template messages only.
    config=None: MedCart Tech's number (WHATSAPP_* settings)."""
    api = "https://graph.facebook.com"

    def __init__(self, config: dict | None = None):
        self.config = config

    def settings_for(self) -> dict:
        if self.config:
            return self.config
        return {"phone_number_id": settings.whatsapp_phone_number_id, "access_token": settings.whatsapp_access_token,
                "receipt_template": settings.whatsapp_receipt_template,
                "language": settings.whatsapp_template_language, "api_version": settings.whatsapp_api_version}

    def send_template(self, recipient: str, template: str | None, params: list[str]) -> str:
        c = self.settings_for()
        if not c.get("phone_number_id") or not c.get("access_token"):
            raise NotConfigured("WhatsApp is not configured (Business phone number id / access token)")
        if not template:
            raise NotConfigured("No approved WhatsApp template is configured for this message")
        payload = {
            "messaging_product": "whatsapp", "to": recipient.lstrip("+").replace(" ", ""), "type": "template",
            "template": {"name": template, "language": {"code": c.get("language") or "en"},
                         "components": [{"type": "body",
                                         "parameters": [{"type": "text", "text": str(p)[:1000]} for p in params]}]},
        }
        version = c.get("api_version") or settings.whatsapp_api_version
        status, body = http_post_json(f"{self.api}/{version}/{c['phone_number_id']}/messages", payload,
                                      {"Authorization": f"Bearer {c['access_token']}"})
        if status >= 300:
            error = (body.get("error") or {}).get("message") or f"HTTP {status}"
            if status in (400, 401, 403):  # bad template / token: retrying will not help
                raise PermanentError(f"WhatsApp refused the message: {error}")
            raise DeliveryError(f"WhatsApp error: {error}")
        messages = body.get("messages") or []
        return messages[0]["id"] if messages else ""


class PermanentError(DeliveryError):
    pass


# Replaced in tests.
email_sender = EmailSender()
sms_sender = SmsSender()
whatsapp_sender = WhatsAppSender()


def channel_status() -> dict:
    return {
        "email": {"configured": bool(settings.smtp_host and settings.smtp_from)},
        "sms": {"configured": settings.sms_provider == "log"
                or (settings.sms_provider == "webhook" and bool(settings.sms_webhook_url)),
                "provider": settings.sms_provider},
        "whatsapp": {"configured": bool(settings.whatsapp_phone_number_id and settings.whatsapp_access_token)},
    }


# ------------------------------------------------------------
# Queueing
# ------------------------------------------------------------

def enqueue(conn: psycopg.Connection, *, channel: str, recipient: str, body: str, subject: str | None = None,
            user_id: int | None = None, notification_id: int | None = None,
            scheduled_report_id: int | None = None, attachment: tuple[str, str, bytes] | None = None,
            message_type: str = "ALERT", dispensation_id: int | None = None, template_name: str | None = None,
            template_params: list | None = None, consent_status: str | None = None) -> int:
    name, content_type, content = attachment if attachment else (None, None, None)
    return conn.execute(
        """
        INSERT INTO notification_deliveries
            (channel, recipient, subject, body, user_id, notification_id, scheduled_report_id,
             attachment_name, attachment_type, attachment, message_type, dispensation_id, template_name,
             template_params, consent_status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (channel, recipient, subject, body, user_id, notification_id, scheduled_report_id,
         name, content_type, content, message_type, dispensation_id, template_name,
         json.dumps(template_params) if template_params is not None else None, consent_status),
    ).fetchone()["id"]


def enqueue_for_notification(conn: psycopg.Connection, notification_id: int, severity: str, title: str,
                             message: str) -> int:
    """Queue e-mail / SMS for every user of the organization who opted in at
    this severity. Returns the number of messages queued."""
    users = conn.execute(
        """
        SELECT id, email, phone, notify_email, notify_sms, notify_min_severity
        FROM users
        WHERE organization_id = current_org() AND is_active
          AND ((notify_email AND email IS NOT NULL) OR (notify_sms AND phone IS NOT NULL))
        """
    ).fetchall()
    queued = 0
    link = f"\n\nOpen PharmaStock: {settings.app_base_url}/app/#/notifications" if settings.app_base_url else ""
    for user in users:
        if SEVERITY_RANK[severity] < SEVERITY_RANK[user["notify_min_severity"]]:
            continue
        if user["notify_email"] and user["email"]:
            enqueue(conn, channel="EMAIL", recipient=user["email"], subject=f"[PharmaStock {severity}] {title}",
                    body=f"{title}\n\n{message}{link}\n\nYou receive this because of your notification "
                         "preferences in PharmaStock.",
                    user_id=user["id"], notification_id=notification_id)
            queued += 1
        if user["notify_sms"] and user["phone"]:
            enqueue(conn, channel="SMS", recipient=user["phone"], body=f"PharmaStock {severity}: {title}"[:300],
                    user_id=user["id"], notification_id=notification_id)
            queued += 1
    return queued


# ------------------------------------------------------------
# Sending (background worker)
# ------------------------------------------------------------

def _organization(conn) -> dict:
    return conn.execute("SELECT id, messaging_mode FROM organizations WHERE id = current_org()").fetchone()


def _own_provider(conn, channel: str) -> dict | None:
    from .. import crypto
    row = conn.execute("SELECT provider, config_encrypted FROM messaging_providers WHERE channel = %s AND is_active",
                       (channel,)).fetchone()
    if row is None:
        return None
    return {"provider": row["provider"], **json.loads(crypto.decrypt(row["config_encrypted"]))}


def has_consent(conn, row: dict) -> bool:
    """Customer consent, checked at sending time."""
    if row["channel"] == "EMAIL":
        sale = conn.execute("SELECT consent_email FROM dispensations WHERE id = %s", (row["dispensation_id"],)).fetchone()
        return bool(sale and sale["consent_email"])
    if conn.execute(
        "SELECT 1 FROM platform_opt_outs WHERE channel = %s "
        "AND right(regexp_replace(phone, '\\D', '', 'g'), 9) = right(regexp_replace(%s, '\\D', '', 'g'), 9)",
        (row["channel"], row["recipient"]),
    ).fetchone():
        return False
    consent = conn.execute("SELECT status FROM message_consents WHERE phone = %s AND channel = %s",
                           (row["recipient"], row["channel"])).fetchone()
    return bool(consent and consent["status"] == "OPTED_IN")


FEATURE_FOR_CHANNEL = {"WHATSAPP": "whatsapp", "SMS": "sms", "EMAIL": "email_receipts"}


def _route(conn, row: dict) -> tuple[str, object, bool]:
    """(provider name, sender, charge a credit) for one message."""
    if row["message_type"] not in CUSTOMER_TYPES:
        if row["channel"] == "EMAIL":
            return "smtp", email_sender, False
        if row["channel"] == "SMS":
            return "sms", sms_sender, False
        raise NotConfigured("Staff messages are not sent on WhatsApp")
    from . import plans
    org = _organization(conn)
    if not plans.has_feature(conn, org["id"], FEATURE_FOR_CHANNEL[row["channel"]]):
        raise NotConfigured(f"{row['channel'].title()} messages are not included in the plan")
    if not has_consent(conn, row):
        raise NotConfigured("The customer has not consented (or has opted out)")
    mode = org["messaging_mode"]
    if mode == "DISABLED":
        raise NotConfigured("Customer messaging is turned off (Settings → Messaging)")
    if mode == "OWN_PROVIDER":
        config = _own_provider(conn, row["channel"])
        if config is None:
            raise NotConfigured(f"No {row['channel'].title()} provider is set up for this organization")
        sender = {"WHATSAPP": WhatsAppSender, "SMS": SmsSender, "EMAIL": EmailSender}[row["channel"]](config)
        return config["provider"], sender, False
    # PLATFORM_CREDITS: MedCart Tech's accounts; WhatsApp and SMS cost one credit.
    sender = {"WHATSAPP": whatsapp_sender, "SMS": sms_sender, "EMAIL": email_sender}[row["channel"]]
    return {"WHATSAPP": "whatsapp_cloud", "SMS": "sms", "EMAIL": "smtp"}[row["channel"]], sender, \
        row["channel"] in ("WHATSAPP", "SMS")


def _send(row: dict, sender) -> str | None:
    if row["channel"] == "EMAIL":
        attachment = ((row["attachment_name"], row["attachment_type"], bytes(row["attachment"]))
                      if row["attachment"] is not None else None)
        sender.send(row["recipient"], row["subject"] or "PharmaStock", row["body"], attachment)
        return None
    if row["channel"] == "SMS":
        return sender.send(row["recipient"], row["body"])
    return sender.send_template(row["recipient"], row["template_name"] or sender.settings_for().get(
        "thank_you_template" if row["message_type"] == "THANK_YOU" else "receipt_template"),
        row["template_params"] or [])


def _refund(conn, row: dict, reason: str) -> None:
    if row["credits_charged"]:
        conn.execute("INSERT INTO messaging_credit_ledger (change, reason, delivery_id, note) "
                     "VALUES (%s, 'REFUND', %s, %s)", (row["credits_charged"], row["id"], reason[:200]))
        conn.execute("UPDATE notification_deliveries SET credits_charged = 0 WHERE id = %s", (row["id"],))


def process_outbox(conn: psycopg.Connection, limit: int = 50) -> dict:
    """Send due messages for the current organization. Each message is sent
    and recorded in its own transaction."""
    from . import billing

    counts = {"sent": 0, "failed": 0, "retry": 0, "skipped": 0}
    conn.commit()  # start from a fresh transaction so "now" is current
    for _ in range(limit):
        row = conn.execute(
            """
            SELECT id, channel, recipient, subject, body, attachment_name, attachment_type, attachment, attempts,
                   message_type, dispensation_id, template_name, template_params, credits_charged
            FROM notification_deliveries
            WHERE status = 'PENDING' AND next_attempt_at <= CURRENT_TIMESTAMP
            ORDER BY next_attempt_at, id
            LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        ).fetchone()
        if row is None:
            conn.commit()
            break
        provider = None
        try:
            provider, sender, charge = _route(conn, row)
            if charge and not row["credits_charged"]:
                if not billing.charge_message(conn, row["id"]):
                    raise NotConfigured("Not enough messaging credits (Billing → Messaging credits)")
                conn.execute("UPDATE notification_deliveries SET credits_charged = 1 WHERE id = %s", (row["id"],))
                row["credits_charged"] = 1
            message_id = _send(row, sender)
        except NotConfigured as error:
            conn.execute("UPDATE notification_deliveries SET status = 'SKIPPED', last_error = %s, provider = %s, "
                         "attempts = attempts + 1 WHERE id = %s", (str(error), provider, row["id"]))
            _refund(conn, row, "not sent")
            counts["skipped"] += 1
        except Exception as error:  # noqa: BLE001 - any sender failure is retried
            attempts = row["attempts"] + 1
            final = attempts >= MAX_ATTEMPTS or isinstance(error, PermanentError)
            conn.execute(
                """
                UPDATE notification_deliveries
                SET attempts = %s, last_error = %s, status = %s, provider = %s,
                    next_attempt_at = CURRENT_TIMESTAMP + make_interval(mins => %s)
                WHERE id = %s
                """,
                (attempts, str(error)[:1000], "FAILED" if final else "PENDING", provider, 2 ** attempts, row["id"]),
            )
            if final:
                _refund(conn, row, "delivery failed")
            logger.warning("Delivery %s via %s failed (attempt %s): %s", row["id"], row["channel"], attempts, error)
            counts["failed" if final else "retry"] += 1
        else:
            conn.execute(
                """
                UPDATE notification_deliveries SET status = 'SENT', sent_at = CURRENT_TIMESTAMP, attempts = attempts + 1,
                       last_error = NULL, provider = %s, provider_message_id = %s, delivery_status = 'SENT',
                       delivery_status_at = CURRENT_TIMESTAMP
                WHERE id = %s
                """,
                (provider, message_id or None, row["id"]),
            )
            if message_id:
                conn.execute(
                    "INSERT INTO provider_message_routes (provider, provider_message_id, organization_id, delivery_id) "
                    "VALUES (%s, %s, current_org(), %s) ON CONFLICT DO NOTHING", (provider, message_id, row["id"]))
            counts["sent"] += 1
        conn.commit()
    return counts
