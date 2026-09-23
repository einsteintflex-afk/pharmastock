# ============================================================
# NOTIFICATION DELIVERY: E-MAIL AND SMS OUTBOX
# ============================================================
# Messages are queued in notification_deliveries inside the transaction that
# raised them and sent later by process_outbox() (background worker), with
# exponential back-off: a mail-server outage never blocks stock work and
# messages are not lost. Channels that are not configured mark messages
# SKIPPED with the reason, so administrators can see why nothing arrived.

import json
import logging
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from email.utils import make_msgid

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

class EmailSender:
    def send(self, recipient: str, subject: str, body: str, attachment: tuple[str, str, bytes] | None) -> None:
        if not settings.smtp_host or not settings.smtp_from:
            raise NotConfigured("E-mail is not configured (SMTP_HOST / SMTP_FROM)")
        message = EmailMessage()
        message["From"] = settings.smtp_from
        message["To"] = recipient
        message["Subject"] = subject
        message["Message-ID"] = make_msgid(domain="pharmastock")
        message.set_content(body)
        if attachment:
            name, content_type, content = attachment
            maintype, _, subtype = content_type.partition("/")
            message.add_attachment(content, maintype=maintype, subtype=subtype or "octet-stream", filename=name)

        context = ssl.create_default_context()
        if settings.smtp_security == "ssl":
            client = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=30, context=context)
        else:
            client = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30)
        try:
            if settings.smtp_security == "starttls":
                client.starttls(context=context)
            if settings.smtp_username:
                client.login(settings.smtp_username, settings.smtp_password or "")
            client.send_message(message)
        except (smtplib.SMTPException, OSError) as error:
            raise DeliveryError(f"SMTP error: {error}") from error
        finally:
            try:
                client.quit()
            except (smtplib.SMTPException, OSError):
                pass


class SmsSender:
    def send(self, recipient: str, text: str) -> None:
        provider = settings.sms_provider
        if provider == "log":
            logger.info("SMS to %s: %s", recipient, text)
            return
        if provider != "webhook" or not settings.sms_webhook_url:
            raise NotConfigured("SMS is not configured (SMS_PROVIDER / SMS_WEBHOOK_URL)")
        request = urllib.request.Request(
            settings.sms_webhook_url,
            data=json.dumps({"to": recipient, "message": text}).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {settings.sms_webhook_token}"}
                        if settings.sms_webhook_token else {})},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 (configured URL)
                if response.status >= 300:
                    raise DeliveryError(f"SMS gateway returned HTTP {response.status}")
        except OSError as error:
            raise DeliveryError(f"SMS gateway error: {error}") from error


# Replaced in tests.
email_sender = EmailSender()
sms_sender = SmsSender()


def channel_status() -> dict:
    return {
        "email": {"configured": bool(settings.smtp_host and settings.smtp_from)},
        "sms": {"configured": settings.sms_provider == "log"
                or (settings.sms_provider == "webhook" and bool(settings.sms_webhook_url)),
                "provider": settings.sms_provider},
    }


# ------------------------------------------------------------
# Queueing
# ------------------------------------------------------------

def enqueue(conn: psycopg.Connection, *, channel: str, recipient: str, body: str, subject: str | None = None,
            user_id: int | None = None, notification_id: int | None = None,
            scheduled_report_id: int | None = None, attachment: tuple[str, str, bytes] | None = None) -> int:
    name, content_type, content = attachment if attachment else (None, None, None)
    return conn.execute(
        """
        INSERT INTO notification_deliveries
            (channel, recipient, subject, body, user_id, notification_id, scheduled_report_id,
             attachment_name, attachment_type, attachment)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (channel, recipient, subject, body, user_id, notification_id, scheduled_report_id,
         name, content_type, content),
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

def process_outbox(conn: psycopg.Connection, limit: int = 50) -> dict:
    """Send due messages for the current organization. Each message is sent
    and recorded in its own transaction."""
    counts = {"sent": 0, "failed": 0, "retry": 0, "skipped": 0}
    conn.commit()  # start from a fresh transaction so "now" is current
    for _ in range(limit):
        row = conn.execute(
            """
            SELECT id, channel, recipient, subject, body, attachment_name, attachment_type, attachment, attempts
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
        try:
            if row["channel"] == "EMAIL":
                attachment = ((row["attachment_name"], row["attachment_type"], bytes(row["attachment"]))
                              if row["attachment"] is not None else None)
                email_sender.send(row["recipient"], row["subject"] or "PharmaStock", row["body"], attachment)
            else:
                sms_sender.send(row["recipient"], row["body"])
        except NotConfigured as error:
            conn.execute("UPDATE notification_deliveries SET status = 'SKIPPED', last_error = %s, "
                         "attempts = attempts + 1 WHERE id = %s", (str(error), row["id"]))
            counts["skipped"] += 1
        except Exception as error:  # noqa: BLE001 - any sender failure is retried
            attempts = row["attempts"] + 1
            final = attempts >= MAX_ATTEMPTS
            conn.execute(
                """
                UPDATE notification_deliveries
                SET attempts = %s, last_error = %s, status = %s,
                    next_attempt_at = CURRENT_TIMESTAMP + make_interval(mins => %s)
                WHERE id = %s
                """,
                (attempts, str(error)[:1000], "FAILED" if final else "PENDING", 2 ** attempts, row["id"]),
            )
            logger.warning("Delivery %s via %s failed (attempt %s): %s", row["id"], row["channel"], attempts, error)
            counts["failed" if final else "retry"] += 1
        else:
            conn.execute("UPDATE notification_deliveries SET status = 'SENT', sent_at = CURRENT_TIMESTAMP, "
                         "attempts = attempts + 1, last_error = NULL WHERE id = %s", (row["id"],))
            counts["sent"] += 1
        conn.commit()
    return counts
