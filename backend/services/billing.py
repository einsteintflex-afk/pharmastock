# ============================================================
# SUBSCRIPTIONS, PAYMENTS AND MESSAGING CREDITS
# ============================================================
# Payment providers sit behind one small interface (create a checkout;
# verify a webhook). A subscription or a credit purchase is applied ONLY
# after the provider confirms the payment through a signature-verified
# webhook, or after a platform administrator confirms a manual payment
# (bank transfer / mobile money received by MedCart Tech). A browser
# redirect or any client-side "success" is never trusted.
#
# Prices are data (plans.price_monthly / price_annual, messaging_packages.price)
# set by MedCart Tech. Nothing is sold while its price is empty.

import hashlib
import hmac
import json
import logging
import secrets
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

import psycopg
from fastapi import HTTPException

from ..config import settings
from . import plans

logger = logging.getLogger("pharmastock.billing")


# ------------------------------------------------------------
# Providers
# ------------------------------------------------------------

@dataclass
class Checkout:
    checkout_url: str | None
    instructions: str | None = None


class ManualProvider:
    """Invoice / bank transfer / mobile money arranged with MedCart Tech; a
    platform administrator confirms receipt in the platform console."""
    name = "manual"

    def create_checkout(self, payment: dict, email: str | None, callback_url: str) -> Checkout:
        return Checkout(None, f"Pay {payment['currency']} {payment['amount']:.2f} to MedCart Tech quoting reference "
                              f"{payment['reference']}. Your plan or credits are activated when MedCart Tech "
                              "confirms the payment.")

    def verify_webhook(self, body: bytes, headers) -> dict | None:
        return None


class PaystackProvider:
    """Paystack (cards, mobile money). Webhooks are verified with the
    HMAC-SHA512 signature of the raw body (x-paystack-signature)."""
    name = "paystack"
    api = "https://api.paystack.co"

    def __init__(self, secret_key: str):
        self.secret_key = secret_key

    def create_checkout(self, payment: dict, email: str | None, callback_url: str) -> Checkout:
        if not email:
            raise HTTPException(status_code=400, detail="An e-mail address is needed for card / mobile money checkout. "
                                                        "Add one to your user profile.")
        body = json.dumps({
            "email": email, "reference": payment["reference"], "currency": payment["currency"],
            "amount": int((Decimal(str(payment["amount"])) * 100).to_integral_value()),
            "callback_url": callback_url, "metadata": {"payment_id": payment["id"]},
        }).encode()
        request = urllib.request.Request(f"{self.api}/transaction/initialize", data=body, method="POST", headers={
            "Authorization": f"Bearer {self.secret_key}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 (fixed provider URL)
                data = json.loads(response.read())
        except Exception as error:
            logger.warning("Paystack initialize failed: %s", error)
            raise HTTPException(status_code=502, detail="The payment provider is unavailable. Try again later.")
        if not data.get("status"):
            raise HTTPException(status_code=502, detail="The payment provider refused the request.")
        return Checkout(data["data"]["authorization_url"])

    def verify_webhook(self, body: bytes, headers) -> dict | None:
        signature = headers.get("x-paystack-signature", "")
        expected = hmac.new(self.secret_key.encode(), body, hashlib.sha512).hexdigest()
        if not signature or not hmac.compare_digest(signature, expected):
            return None
        event = json.loads(body)
        data = event.get("data") or {}
        if event.get("event") != "charge.success" or data.get("status") != "success":
            return {"ignored": True, "event": event.get("event")}
        return {"reference": data.get("reference"), "amount_minor": data.get("amount"),
                "currency": data.get("currency")}


def provider():
    if settings.payment_provider == "paystack":
        if not settings.paystack_secret_key:
            raise HTTPException(status_code=503, detail="Online payment is not configured")
        return PaystackProvider(settings.paystack_secret_key)
    return ManualProvider()


# ------------------------------------------------------------
# Checkout
# ------------------------------------------------------------

def create_payment(conn: psycopg.Connection, organization_id: int, user_id: int, *, purpose: str,
                   plan_code: str | None = None, billing_cycle: str | None = None,
                   package_code: str | None = None) -> dict:
    if purpose == "SUBSCRIPTION":
        plan = plans.catalogue(conn).get(plan_code or "")
        if plan is None or not plan["is_active"]:
            raise HTTPException(status_code=400, detail="Unknown plan")
        price = plan["price_monthly"] if billing_cycle == "MONTHLY" else plan["price_annual"]
        currency = plan["currency"]
        if price is None or not currency:
            raise HTTPException(status_code=400, detail="This plan is not sold online. Contact MedCart Tech.")
    else:
        package = conn.execute("SELECT * FROM messaging_packages WHERE code = %s AND is_active",
                               (package_code,)).fetchone()
        if package is None or package["price"] is None or not package["currency"]:
            raise HTTPException(status_code=400, detail="This credit package is not available")
        price, currency = package["price"], package["currency"]
    return conn.execute(
        """
        INSERT INTO payments (organization_id, purpose, plan_code, billing_cycle, package_code, amount, currency,
                              provider, reference, created_by)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING *
        """,
        (organization_id, purpose, plan_code if purpose == "SUBSCRIPTION" else None,
         billing_cycle if purpose == "SUBSCRIPTION" else None,
         package_code if purpose == "MESSAGING_CREDITS" else None, price, currency,
         settings.payment_provider, f"PS-{organization_id}-{secrets.token_hex(8)}", user_id),
    ).fetchone()


# ------------------------------------------------------------
# Applying a confirmed payment (exactly once)
# ------------------------------------------------------------

def mark_paid(conn: psycopg.Connection, reference: str, *, amount: Decimal | None = None,
              currency: str | None = None, confirmed_by: int | None = None) -> dict:
    """Mark a payment PAID and apply it. Idempotent: a repeated webhook for
    the same reference changes nothing."""
    payment = conn.execute("SELECT * FROM payments WHERE reference = %s FOR UPDATE", (reference,)).fetchone()
    if payment is None:
        raise HTTPException(status_code=404, detail="Unknown payment reference")
    if payment["applied_at"] is not None:
        return {**payment, "already_applied": True}
    if payment["status"] not in ("PENDING", "FAILED"):
        raise HTTPException(status_code=409, detail=f"Payment is {payment['status']}")
    if amount is not None and (Decimal(str(amount)) != Decimal(str(payment["amount"]))
                               or (currency or "").upper() != payment["currency"].upper()):
        conn.execute("UPDATE payments SET status = 'FAILED', failure_reason = %s WHERE id = %s",
                     (f"amount mismatch: got {currency} {amount}", payment["id"]))
        raise HTTPException(status_code=400, detail="Paid amount does not match the payment")

    if payment["purpose"] == "SUBSCRIPTION":
        _apply_subscription(conn, payment, confirmed_by)
    else:
        package = conn.execute("SELECT credits FROM messaging_packages WHERE code = %s",
                               (payment["package_code"],)).fetchone()
        add_credits(conn, payment["organization_id"], package["credits"], "PURCHASE",
                    payment_id=payment["id"], created_by=confirmed_by,
                    note=f"Package {payment['package_code']}")
    return conn.execute(
        "UPDATE payments SET status = 'PAID', paid_at = CURRENT_TIMESTAMP, applied_at = CURRENT_TIMESTAMP, "
        "failure_reason = NULL WHERE id = %s RETURNING *", (payment["id"],)).fetchone()


def _apply_subscription(conn, payment: dict, user_id: int | None) -> None:
    org = plans.organization(conn, payment["organization_id"])
    start = max(date.today(), (org["current_period_end"].date() if org["current_period_end"] else date.today()))
    end = _add_months(start, 12 if payment["billing_cycle"] == "ANNUAL" else 1)
    conn.execute(
        """
        INSERT INTO subscriptions (organization_id, plan_code, billing_cycle, status, current_period_start,
                                   current_period_end, created_by)
        VALUES (%s, %s, %s, 'ACTIVE', %s, %s, %s)
        """,
        (org["id"], payment["plan_code"], payment["billing_cycle"], start, end, user_id),
    )
    conn.execute(
        """
        UPDATE organizations SET plan = %s, current_period_end = %s, subscription_status = 'ACTIVE',
               status = CASE WHEN status = 'TRIAL' THEN 'ACTIVE' ELSE status END
        WHERE id = %s
        """,
        (payment["plan_code"], datetime.combine(end, datetime.min.time()), org["id"]),
    )


def _add_months(day: date, months: int) -> date:
    month = day.month - 1 + months
    year = day.year + month // 12
    month = month % 12 + 1
    last = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31,
            30, 31][month - 1]
    return date(year, month, min(day.day, last))


def expire_due(conn: psycopg.Connection) -> int:
    """Daily: paid periods and trials that have ended become PAST_DUE /
    TRIAL_ENDED. Organizations are NOT suspended automatically: MedCart Tech
    decides (grace periods are a business decision)."""
    rows = conn.execute(
        """
        UPDATE organizations SET subscription_status = CASE WHEN status = 'TRIAL' THEN 'TRIAL_ENDED' ELSE 'PAST_DUE' END
        WHERE status IN ('TRIAL', 'ACTIVE')
          AND COALESCE(subscription_status, '') NOT IN ('PAST_DUE', 'TRIAL_ENDED')
          AND ((status = 'TRIAL' AND trial_ends_at < CURRENT_TIMESTAMP)
               OR (current_period_end IS NOT NULL AND current_period_end < CURRENT_TIMESTAMP))
        RETURNING id
        """
    ).fetchall()
    return len(rows)


# ------------------------------------------------------------
# Messaging credits (ledger; balance = sum of changes)
# ------------------------------------------------------------

def credit_balance(conn: psycopg.Connection) -> int:
    """Balance of the organization set on the connection (row level security)."""
    return int(conn.execute("SELECT COALESCE(SUM(change), 0) AS n FROM messaging_credit_ledger").fetchone()["n"])


def add_credits(conn: psycopg.Connection, organization_id: int, change: int, reason: str, *,
                payment_id: int | None = None, created_by: int | None = None, note: str | None = None,
                delivery_id: int | None = None) -> None:
    from ..database import set_organization

    previous = conn.execute("SELECT current_setting('app.organization_id', true) AS org").fetchone()["org"]
    set_organization(conn, organization_id)
    conn.execute(
        """
        INSERT INTO messaging_credit_ledger (change, reason, payment_id, delivery_id, note, created_by)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (change, reason, payment_id, delivery_id, note, created_by),
    )
    set_organization(conn, int(previous) if previous else None)


def charge_message(conn: psycopg.Connection, delivery_id: int, credits: int = 1) -> bool:
    """Deduct credits for one message (current organization). False when the
    balance is too low (the message is then not sent). Serialized per
    organization with an advisory lock so two workers cannot overspend."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('credits'), current_org())")
    if credit_balance(conn) < credits:
        return False
    conn.execute(
        "INSERT INTO messaging_credit_ledger (change, reason, delivery_id) VALUES (%s, 'MESSAGE', %s)",
        (-credits, delivery_id),
    )
    return True
