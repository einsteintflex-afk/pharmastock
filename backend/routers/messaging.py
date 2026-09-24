# ============================================================
# MESSAGING SETTINGS, CONSENT AND PROVIDER WEBHOOKS
# ============================================================

import asyncio
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from .. import audit, database
from ..config import settings
from ..database import get_db
from ..schemas import Phone
from ..security import CurrentUser, require
from ..services import billing, delivery, messaging, plans

router = APIRouter(tags=["Messaging"])

Channel = Literal["WHATSAPP", "SMS", "EMAIL"]


class ModeBody(BaseModel):
    mode: Literal["DISABLED", "PLATFORM_CREDITS", "OWN_PROVIDER"]


class ProviderBody(BaseModel):
    config: dict = Field(default_factory=dict)


class ConsentBody(BaseModel):
    phone: Phone
    channel: Literal["WHATSAPP", "SMS"]
    status: Literal["OPTED_IN", "OPTED_OUT"]


class TestBody(BaseModel):
    channel: Channel
    recipient: str = Field(min_length=3, max_length=150)


@router.get("/messaging")
def messaging_settings(user: CurrentUser = Depends(require("communications.manage")),
                       conn: psycopg.Connection = Depends(get_db)):
    org = plans.organization(conn, user.organization_id)
    counts = conn.execute(
        """
        SELECT channel, message_type, COUNT(*) FILTER (WHERE status = 'SENT') AS sent,
               COUNT(*) FILTER (WHERE status = 'SKIPPED') AS skipped, COUNT(*) FILTER (WHERE status = 'FAILED') AS failed,
               COUNT(*) FILTER (WHERE delivery_status IN ('DELIVERED', 'READ')) AS delivered
        FROM notification_deliveries WHERE created_at >= date_trunc('month', CURRENT_DATE)
          AND message_type IN ('RECEIPT', 'THANK_YOU', 'TEST')
        GROUP BY channel, message_type ORDER BY channel, message_type
        """
    ).fetchall()
    return {
        "mode": org["messaging_mode"],
        "features": {c: plans.has_feature(conn, user.organization_id, f)
                     for c, f in delivery.FEATURE_FOR_CHANNEL.items()},
        "providers": messaging.provider_summary(conn),
        "provider_fields": {c: {k: v for k, v in spec.items() if k != "provider"}
                            for c, spec in messaging.PROVIDER_FIELDS.items()},
        "platform_channels": delivery.channel_status(),
        "credit_balance": billing.credit_balance(conn),
        "this_month": counts,
        "webhook_url": (settings.app_base_url or "") + "/messaging/webhooks/whatsapp",
    }


@router.put("/messaging/mode")
def set_mode(body: ModeBody, user: CurrentUser = Depends(require("communications.manage")),
             conn: psycopg.Connection = Depends(get_db)):
    """Explicit activation: customer messages are only sent after the
    organization chooses how (its own accounts, or MedCart Tech credits)."""
    old = plans.organization(conn, user.organization_id)["messaging_mode"]
    conn.execute("UPDATE organizations SET messaging_mode = %s WHERE id = %s", (body.mode, user.organization_id))
    audit.record(conn, user, "MESSAGING_MODE", "organization", user.organization_id, {"mode": old}, {"mode": body.mode})
    conn.commit()
    return {"mode": body.mode}


@router.put("/messaging/providers/{channel}")
def save_provider(channel: Channel, body: ProviderBody, user: CurrentUser = Depends(require("communications.manage")),
                  conn: psycopg.Connection = Depends(get_db)):
    messaging.save_provider(conn, user.organization_id, user.id, channel, body.config)
    # The audit records which fields changed, never their values.
    audit.record(conn, user, "MESSAGING_PROVIDER_SAVED", "messaging_provider", channel, None,
                 {"channel": channel, "fields": sorted(k for k, v in body.config.items() if v not in (None, ""))})
    conn.commit()
    return messaging.provider_summary(conn).get(channel)


@router.delete("/messaging/providers/{channel}")
def delete_provider(channel: Channel, user: CurrentUser = Depends(require("communications.manage")),
                    conn: psycopg.Connection = Depends(get_db)):
    if not messaging.delete_provider(conn, user.organization_id, channel):
        raise HTTPException(status_code=404, detail="No provider set up for this channel")
    audit.record(conn, user, "MESSAGING_PROVIDER_REMOVED", "messaging_provider", channel)
    conn.commit()
    return {"removed": channel}


@router.post("/messaging/test", status_code=202)
def send_test(body: TestBody, user: CurrentUser = Depends(require("communications.manage")),
              conn: psycopg.Connection = Depends(get_db)):
    """Queue a test customer message through the organization's messaging
    mode (consent is recorded for the test number by the person testing)."""
    if body.channel != "EMAIL":
        conn.execute(
            """
            INSERT INTO message_consents (phone, channel, status, source, recorded_by)
            VALUES (%s, %s, 'OPTED_IN', 'TEST', %s)
            ON CONFLICT (organization_id, phone, channel) DO NOTHING
            """,
            (body.recipient, body.channel, user.id),
        )
    delivery_id = delivery.enqueue(
        conn, channel=body.channel, recipient=body.recipient, message_type="RECEIPT",
        subject="PharmaStock test message", body="This is a test message from PharmaStock.",
        template_params=["Test", user.organization_name or "PharmaStock", "0.00", "https://example.com"],
        consent_status="TEST", user_id=user.id)
    audit.record(conn, user, "MESSAGING_TEST", "notification_delivery", delivery_id, None, {"channel": body.channel})
    conn.commit()
    return {"id": delivery_id, "status": "PENDING"}


@router.get("/messaging/consents")
def list_consents(status: str | None = None, limit: int = Query(200, ge=1, le=1000),
                  user: CurrentUser = Depends(require("communications.manage")),
                  conn: psycopg.Connection = Depends(get_db)):
    return conn.execute(
        """
        SELECT c.id, c.phone, c.channel, c.status, c.source, c.recorded_at, u.full_name AS recorded_by_name
        FROM message_consents c LEFT JOIN users u ON u.id = c.recorded_by
        WHERE (%s::text IS NULL OR c.status = %s::text) ORDER BY c.recorded_at DESC LIMIT %s
        """,
        (status, status, limit),
    ).fetchall()


@router.post("/messaging/consents")
def set_consent(body: ConsentBody, user: CurrentUser = Depends(require("stock.dispense")),
                conn: psycopg.Connection = Depends(get_db)):
    """Record a customer's choice (e.g. an opt-out asked for at the counter)."""
    conn.execute(
        """
        INSERT INTO message_consents (phone, channel, status, source, recorded_by)
        VALUES (%s, %s, %s, 'STAFF', %s)
        ON CONFLICT (organization_id, phone, channel) DO UPDATE SET status = EXCLUDED.status, source = 'STAFF',
            recorded_by = EXCLUDED.recorded_by, recorded_at = CURRENT_TIMESTAMP
        """,
        (body.phone, body.channel, body.status, user.id),
    )
    audit.record(conn, user, "MESSAGE_CONSENT", "message_consent", None, None, body.model_dump())
    conn.commit()
    return {"phone": body.phone, "channel": body.channel, "status": body.status}


# ------------------------------------------------------------
# WhatsApp Business Platform webhook (public)
# ------------------------------------------------------------

@router.get("/messaging/webhooks/whatsapp", include_in_schema=False)
def whatsapp_verify(request: Request):
    """Meta's subscription check: echo the challenge when the verify token matches."""
    params = request.query_params
    token = params.get("hub.verify_token") or ""
    import hmac as _hmac
    if (params.get("hub.mode") == "subscribe" and settings.whatsapp_verify_token
            and _hmac.compare_digest(token, settings.whatsapp_verify_token)):
        return PlainTextResponse(params.get("hub.challenge") or "")
    return JSONResponse(status_code=403, content={"detail": "Verification failed"})


@router.post("/messaging/webhooks/whatsapp", include_in_schema=False)
async def whatsapp_webhook(request: Request):
    body = await request.body()
    if len(body) > 1_000_000:
        return JSONResponse(status_code=413, content={"detail": "Too large"})
    signature = request.headers.get("x-hub-signature-256")

    def apply():
        with database.pool.connection() as conn:
            try:
                result = messaging.apply_whatsapp_webhook(conn, body, signature)
            except HTTPException as error:
                conn.rollback()
                return error.status_code, {"detail": error.detail}
            conn.commit()
            return 200, result

    status, content = await asyncio.to_thread(apply)
    return JSONResponse(status_code=status, content=content)
