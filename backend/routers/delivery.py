# ============================================================
# E-MAIL / SMS DELIVERY AND SCHEDULED REPORTS
# ============================================================

from datetime import datetime
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from .. import audit
from ..database import get_db
from ..pagination import set_total
from ..schemas import Email, Name150, Phone, blank_to_none
from ..security import CurrentUser, require, require_feature
from ..services import delivery, plans, scheduler

router = APIRouter(tags=["Notification delivery"])

Severity = Literal["INFO", "WARNING", "CRITICAL"]


# ------------------------------------------------------------
# Personal preferences
# ------------------------------------------------------------

class Preferences(BaseModel):
    email: Email | None = None
    phone: Phone | None = None
    notify_email: bool = False
    notify_sms: bool = False
    notify_min_severity: Severity = "CRITICAL"


PREF_COLUMNS = "email, phone, notify_email, notify_sms, notify_min_severity"


@router.get("/me/notification-preferences")
def get_preferences(user: CurrentUser = Depends(require("notifications.read")),
                    conn: psycopg.Connection = Depends(get_db)):
    row = conn.execute(f"SELECT {PREF_COLUMNS} FROM users WHERE id = %s", (user.id,)).fetchone()
    return {**row, "channels": delivery.channel_status()}


@router.put("/me/notification-preferences")
def set_preferences(body: Preferences, user: CurrentUser = Depends(require("notifications.read")),
                    conn: psycopg.Connection = Depends(get_db)):
    email, phone = blank_to_none(body.email), blank_to_none(body.phone)
    if body.notify_email and not email:
        raise HTTPException(status_code=400, detail="Add an e-mail address to receive e-mail notifications")
    if body.notify_sms and not phone:
        raise HTTPException(status_code=400, detail="Add a phone number to receive SMS notifications")
    old = conn.execute(f"SELECT {PREF_COLUMNS} FROM users WHERE id = %s", (user.id,)).fetchone()
    row = conn.execute(
        f"""
        UPDATE users SET email = %s, phone = %s, notify_email = %s, notify_sms = %s, notify_min_severity = %s,
               updated_at = CURRENT_TIMESTAMP
        WHERE id = %s AND organization_id = %s
        RETURNING {PREF_COLUMNS}
        """,
        (email, phone, body.notify_email, body.notify_sms, body.notify_min_severity, user.id, user.organization_id),
    ).fetchone()
    before, after = audit.changed_fields(dict(old), dict(row))
    if after:
        audit.record(conn, user, "UPDATE_NOTIFICATION_PREFERENCES", "user", user.id, before, after)
    conn.commit()
    return {**row, "channels": delivery.channel_status()}


# ------------------------------------------------------------
# Outbox (administration)
# ------------------------------------------------------------

@router.get("/notification-deliveries")
def list_deliveries(response: Response,
                    status: Literal["PENDING", "SENT", "FAILED", "SKIPPED"] | None = None,
                    limit: int = Query(default=100, gt=0, le=1000), offset: int = Query(default=0, ge=0),
                    user: CurrentUser = Depends(require("settings.manage")),
                    conn: psycopg.Connection = Depends(get_db)):
    rows = conn.execute(
        """
        SELECT d.id, d.channel, d.recipient, d.subject, d.status, d.attempts, d.last_error, d.created_at,
               d.sent_at, d.next_attempt_at, d.notification_id, d.scheduled_report_id, d.attachment_name,
               users.full_name AS user_name, COUNT(*) OVER () AS total_count
        FROM notification_deliveries d LEFT JOIN users ON users.id = d.user_id
        WHERE (%(status)s::text IS NULL OR d.status = %(status)s::text)
        ORDER BY d.created_at DESC, d.id DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        {"status": status, "limit": limit, "offset": offset},
    ).fetchall()
    set_total(response, rows)
    return {"channels": delivery.channel_status(), "deliveries": rows}


class TestMessage(BaseModel):
    channel: Literal["EMAIL", "SMS"] = "EMAIL"


@router.post("/notification-deliveries/test", status_code=202)
def send_test(body: TestMessage, user: CurrentUser = Depends(require("settings.manage")),
              conn: psycopg.Connection = Depends(get_db)):
    """Queue a test message to your own e-mail address / phone."""
    me = conn.execute("SELECT email, phone FROM users WHERE id = %s", (user.id,)).fetchone()
    recipient = me["email"] if body.channel == "EMAIL" else me["phone"]
    if not recipient:
        raise HTTPException(status_code=400, detail="Add your e-mail address / phone in notification preferences")
    delivery_id = delivery.enqueue(conn, channel=body.channel, recipient=recipient,
                                   subject="PharmaStock test message",
                                   body="This is a test message from PharmaStock. Delivery is working.",
                                   user_id=user.id)
    audit.record(conn, user, "TEST_DELIVERY", "notification_delivery", delivery_id, None, {"channel": body.channel})
    conn.commit()
    return {"id": delivery_id, "status": "PENDING", "channels": delivery.channel_status()}


@router.post("/notification-deliveries/{delivery_id}/retry")
def retry_delivery(delivery_id: int, user: CurrentUser = Depends(require("settings.manage")),
                   conn: psycopg.Connection = Depends(get_db)):
    row = conn.execute(
        """
        UPDATE notification_deliveries SET status = 'PENDING', attempts = 0, next_attempt_at = CURRENT_TIMESTAMP
        WHERE id = %s AND status IN ('FAILED', 'SKIPPED') RETURNING id, status
        """,
        (delivery_id,),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No failed or skipped delivery with this id")
    audit.record(conn, user, "RETRY_DELIVERY", "notification_delivery", delivery_id)
    conn.commit()
    return row


# ------------------------------------------------------------
# Scheduled reports
# ------------------------------------------------------------

def _scheduling(permission: str = "settings.manage"):
    return require_feature("scheduled_reports", permission)


class ScheduleBody(BaseModel):
    name: Name150
    report_key: str = Field(max_length=50)
    format: Literal["csv", "xlsx", "pdf"] = "pdf"
    frequency: Literal["DAILY", "WEEKLY", "MONTHLY"]
    day_of_week: int | None = Field(default=None, ge=0, le=6)
    day_of_month: int | None = Field(default=None, ge=1, le=28)
    hour: int = Field(default=7, ge=0, le=23)
    recipients: list[Email] = Field(min_length=1, max_length=20)
    filters: dict = Field(default_factory=dict)
    is_active: bool = True


SCHEDULE_COLUMNS = ("id, name, report_key, format, frequency, day_of_week, day_of_month, hour, recipients, "
                    "filters, is_active, next_run_at, last_run_at, last_status, created_at")


def _clean(body: ScheduleBody) -> dict:
    data = body.model_dump()
    data["recipients"] = sorted({r.strip().lower() for r in data["recipients"] if r.strip()})
    if not data["recipients"]:
        raise HTTPException(status_code=400, detail="At least one recipient e-mail is required")
    scheduler.validate(data)
    data["next_run_at"] = scheduler.next_run(data["frequency"], data["hour"], data["day_of_week"],
                                             data["day_of_month"], datetime.now())
    return data


@router.get("/scheduled-reports")
def list_schedules(user: CurrentUser = Depends(_scheduling()), conn: psycopg.Connection = Depends(get_db)):
    return conn.execute(f"SELECT {SCHEDULE_COLUMNS} FROM scheduled_reports ORDER BY name").fetchall()


@router.post("/scheduled-reports", status_code=201)
def create_schedule(body: ScheduleBody, user: CurrentUser = Depends(_scheduling()),
                    conn: psycopg.Connection = Depends(get_db)):
    from psycopg.types.json import Jsonb

    plans.check_limit(conn, user.organization_id, "max_scheduled_reports",
                      conn.execute("SELECT COUNT(*) AS n FROM scheduled_reports").fetchone()["n"])
    data = _clean(body)
    row = conn.execute(
        f"""
        INSERT INTO scheduled_reports (name, report_key, format, frequency, day_of_week, day_of_month, hour,
                                       recipients, filters, is_active, next_run_at, created_by)
        VALUES (%(name)s, %(report_key)s, %(format)s, %(frequency)s, %(day_of_week)s, %(day_of_month)s, %(hour)s,
                %(recipients)s, %(filters_json)s, %(is_active)s, %(next_run_at)s, %(user)s)
        RETURNING {SCHEDULE_COLUMNS}
        """,
        {**data, "filters_json": Jsonb(data["filters"]), "user": user.id},
    ).fetchone()
    audit.record(conn, user, "CREATE", "scheduled_report", row["id"], None, dict(row))
    conn.commit()
    return row


@router.put("/scheduled-reports/{schedule_id}")
def update_schedule(schedule_id: int, body: ScheduleBody, user: CurrentUser = Depends(_scheduling()),
                    conn: psycopg.Connection = Depends(get_db)):
    from psycopg.types.json import Jsonb

    old = conn.execute(f"SELECT {SCHEDULE_COLUMNS} FROM scheduled_reports WHERE id = %s FOR UPDATE",
                       (schedule_id,)).fetchone()
    if old is None:
        raise HTTPException(status_code=404, detail="Scheduled report not found")
    data = _clean(body)
    row = conn.execute(
        f"""
        UPDATE scheduled_reports SET name = %(name)s, report_key = %(report_key)s, format = %(format)s,
               frequency = %(frequency)s, day_of_week = %(day_of_week)s, day_of_month = %(day_of_month)s,
               hour = %(hour)s, recipients = %(recipients)s, filters = %(filters_json)s,
               is_active = %(is_active)s, next_run_at = %(next_run_at)s
        WHERE id = %(id)s RETURNING {SCHEDULE_COLUMNS}
        """,
        {**data, "filters_json": Jsonb(data["filters"]), "id": schedule_id},
    ).fetchone()
    before, after = audit.changed_fields(dict(old), dict(row))
    if after:
        audit.record(conn, user, "UPDATE", "scheduled_report", schedule_id, before, after)
    conn.commit()
    return row


@router.delete("/scheduled-reports/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, user: CurrentUser = Depends(_scheduling()),
                    conn: psycopg.Connection = Depends(get_db)):
    row = conn.execute(f"DELETE FROM scheduled_reports WHERE id = %s RETURNING {SCHEDULE_COLUMNS}",
                       (schedule_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Scheduled report not found")
    audit.record(conn, user, "DELETE", "scheduled_report", schedule_id, dict(row), None)
    conn.commit()
    return Response(status_code=204)


@router.post("/scheduled-reports/{schedule_id}/run")
def run_schedule_now(schedule_id: int, user: CurrentUser = Depends(_scheduling()),
                     conn: psycopg.Connection = Depends(get_db)):
    schedule = conn.execute("SELECT * FROM scheduled_reports WHERE id = %s", (schedule_id,)).fetchone()
    if schedule is None:
        raise HTTPException(status_code=404, detail="Scheduled report not found")
    result = scheduler.run(conn, schedule, user)
    conn.commit()
    return result
