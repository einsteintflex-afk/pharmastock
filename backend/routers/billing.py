# ============================================================
# BILLING: SUBSCRIPTION, PAYMENTS, MESSAGING CREDITS
# ============================================================
# Organization owners (billing.manage) see their plan, usage, payments and
# credit balance, and can start a checkout. Activation happens only through
# the verified provider webhook below, or a platform administrator's manual
# confirmation (/platform/payments/{id}/confirm).

import logging
from decimal import Decimal
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .. import audit
from ..config import settings
from ..database import get_db
from ..security import CurrentUser, require
from ..services import billing, organizations, plans

router = APIRouter(tags=["Billing"])
logger = logging.getLogger("pharmastock.billing")


class CheckoutRequest(BaseModel):
    purpose: Literal["SUBSCRIPTION", "MESSAGING_CREDITS"]
    plan_code: str | None = Field(default=None, max_length=30)
    billing_cycle: Literal["MONTHLY", "ANNUAL"] | None = None
    package_code: str | None = Field(default=None, max_length=30)


@router.get("/billing")
def billing_overview(user: CurrentUser = Depends(require("billing.manage")),
                     conn: psycopg.Connection = Depends(get_db)):
    org = organizations.describe(conn, user.organization_id)
    subscription = conn.execute(
        "SELECT * FROM subscriptions WHERE organization_id = %s ORDER BY id DESC LIMIT 1", (user.organization_id,)
    ).fetchone()
    payments = conn.execute(
        """
        SELECT id, purpose, plan_code, billing_cycle, package_code, amount, currency, status, provider, reference,
               created_at, paid_at
        FROM payments WHERE organization_id = %s ORDER BY id DESC LIMIT 50
        """,
        (user.organization_id,),
    ).fetchall()
    packages = conn.execute(
        "SELECT code, label, credits, price, currency FROM messaging_packages WHERE is_active AND price IS NOT NULL "
        "ORDER BY credits").fetchall()
    return {
        "organization": org,
        "subscription_status": plans.organization(conn, user.organization_id)["subscription_status"],
        "subscription": subscription,
        "payments": payments,
        "messaging": {
            "mode": plans.organization(conn, user.organization_id)["messaging_mode"],
            "credit_balance": billing.credit_balance(conn),
            "packages": packages,
        },
        "payment_provider": settings.payment_provider,
    }


@router.get("/billing/credits")
def credit_history(user: CurrentUser = Depends(require("billing.manage")),
                   conn: psycopg.Connection = Depends(get_db)):
    rows = conn.execute(
        "SELECT id, change, reason, payment_id, delivery_id, note, created_at FROM messaging_credit_ledger "
        "ORDER BY id DESC LIMIT 200").fetchall()
    return {"balance": billing.credit_balance(conn), "entries": rows}


@router.post("/billing/checkout", status_code=201)
def start_checkout(body: CheckoutRequest, request: Request, user: CurrentUser = Depends(require("billing.manage")),
                   conn: psycopg.Connection = Depends(get_db)):
    if body.purpose == "SUBSCRIPTION" and not (body.plan_code and body.billing_cycle):
        raise HTTPException(status_code=400, detail="Choose a plan and a billing cycle")
    if body.purpose == "MESSAGING_CREDITS" and not body.package_code:
        raise HTTPException(status_code=400, detail="Choose a credit package")
    payment = billing.create_payment(conn, user.organization_id, user.id, purpose=body.purpose,
                                     plan_code=body.plan_code, billing_cycle=body.billing_cycle,
                                     package_code=body.package_code)
    email = conn.execute("SELECT email FROM users WHERE id = %s", (user.id,)).fetchone()["email"]
    base = settings.app_base_url or str(request.base_url).rstrip("/")
    checkout = billing.provider().create_checkout(payment, email, f"{base}/app/#/billing?reference={payment['reference']}")
    conn.execute("UPDATE payments SET checkout_url = %s WHERE id = %s", (checkout.checkout_url, payment["id"]))
    audit.record(conn, user, "CHECKOUT_STARTED", "payment", payment["id"], None,
                 {"purpose": body.purpose, "plan": body.plan_code, "package": body.package_code,
                  "amount": payment["amount"], "currency": payment["currency"], "reference": payment["reference"]})
    conn.commit()
    return {"payment_id": payment["id"], "reference": payment["reference"], "amount": payment["amount"],
            "currency": payment["currency"], "provider": payment["provider"],
            "checkout_url": checkout.checkout_url, "instructions": checkout.instructions,
            "note": "Your plan or credits change only after the payment provider confirms the payment."}


@router.post("/billing/webhooks/paystack", include_in_schema=False)
async def paystack_webhook(request: Request):
    """Paystack calls this after a payment. Unsigned or wrongly signed calls
    are refused; the paid amount must match the payment exactly."""
    from .. import database

    if settings.payment_provider != "paystack" or not settings.paystack_secret_key:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    body = await request.body()
    verified = billing.PaystackProvider(settings.paystack_secret_key).verify_webhook(body, request.headers)
    if verified is None:
        logger.warning("Paystack webhook with an invalid signature refused")
        return JSONResponse(status_code=401, content={"detail": "Invalid signature"})
    if verified.get("ignored"):
        return {"received": True}

    def apply():
        with database.pool.connection() as conn:
            try:
                payment = billing.mark_paid(conn, verified["reference"],
                                            amount=Decimal(verified["amount_minor"] or 0) / 100,
                                            currency=verified["currency"])
            except HTTPException as error:
                conn.commit()  # keeps a FAILED mark on amount mismatch
                return error.status_code, {"detail": error.detail}
            if not payment.get("already_applied"):
                audit.platform(conn, None, "PAYMENT_CONFIRMED_BY_PROVIDER", payment["organization_id"], "payment",
                               payment["id"], {"reference": payment["reference"], "amount": payment["amount"],
                                               "currency": payment["currency"], "purpose": payment["purpose"]})
            conn.commit()
            return 200, {"received": True}

    import asyncio
    status, content = await asyncio.to_thread(apply)
    return JSONResponse(status_code=status, content=content)
