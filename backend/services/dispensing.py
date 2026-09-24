# ============================================================
# DISPENSING COUNTER
# ============================================================
# A dispensation is one customer transaction (prescription or OTC) with one
# line per medicine. Everything happens in ONE database transaction: if any
# line cannot be filled from usable stock, nothing is dispensed.
#
# Each line is filled FEFO (services/stock.allocate_fefo) and its DISPENSED
# movements are linked to the line, so:
#   * a receipt / record shows exactly which batches the patient received;
#   * a void returns each unit to the batch it came from (RETURNED movements).

from datetime import date
from decimal import ROUND_HALF_UP, Decimal

import secrets

import psycopg
from fastapi import HTTPException

from .. import audit
from ..security import CurrentUser, scoped_location
from . import app_settings, stock

DISPENSE_TYPES = ("PRESCRIPTION", "OTC")
RECEIPT_LINK_DAYS = 90
PAYMENT_METHODS = ("CASH", "MOBILE_MONEY", "CARD", "NHIS", "INSURANCE", "CREDIT", "NO_CHARGE")


def _money(value) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _next_number(conn: psycopg.Connection) -> str:
    """DSP-YYYYMMDD-NNNN, sequential per day. The advisory lock serialises
    concurrent counters so two dispensations never get the same number."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtext('dispensation_number'))")
    prefix = f"DSP-{date.today():%Y%m%d}-"
    row = conn.execute(
        "SELECT MAX(dispensation_number) AS last FROM dispensations WHERE dispensation_number LIKE %s",
        (prefix + "%",),
    ).fetchone()
    last = int(row["last"][len(prefix):]) if row["last"] else 0
    return f"{prefix}{last + 1:04d}"


def create(conn: psycopg.Connection, user: CurrentUser, body: dict) -> dict:
    items = body["items"]
    if not items:
        raise HTTPException(status_code=400, detail="Add at least one medicine")

    medicine_ids = [item["medicine_id"] for item in items]
    if len(set(medicine_ids)) != len(medicine_ids):
        raise HTTPException(status_code=400, detail="Each medicine may appear only once; combine the quantities")

    if body["dispense_type"] == "PRESCRIPTION" and not (body.get("prescription_number") or body.get("prescriber")):
        raise HTTPException(
            status_code=400, detail="A prescription dispensation needs the prescriber or the prescription number",
        )

    medicines = {
        row["id"]: row
        for row in conn.execute(
            "SELECT id, name, strength, dosage_form, selling_price FROM medicines WHERE id = ANY(%s)",
            (medicine_ids,),
        ).fetchall()
    }
    missing = [m for m in medicine_ids if m not in medicines]
    if missing:
        raise HTTPException(status_code=404, detail=f"Medicine not found: {missing}")

    location_id = body.get("location_id")
    # Staff assigned to one location sell from that location only.
    scope = scoped_location(user)
    if scope is not None:
        if location_id is not None and location_id != scope:
            raise HTTPException(status_code=403, detail="You can only dispense from your assigned location")
        location_id = scope
    if location_id is not None:
        row = conn.execute("SELECT is_active FROM locations WHERE id = %s", (location_id,)).fetchone()
        if row is None or not row["is_active"]:
            raise HTTPException(status_code=400, detail="Location not found or inactive")

    consent = body.get("consent") or {}
    if consent.get("whatsapp") or consent.get("sms"):
        if not body.get("patient_phone"):
            raise HTTPException(status_code=400, detail="A phone number is needed to send the receipt by WhatsApp / SMS")
    if consent.get("email") and not body.get("customer_email"):
        raise HTTPException(status_code=400, detail="An e-mail address is needed to e-mail the receipt")

    number = _next_number(conn)
    token = secrets.token_hex(16)
    dispensation = conn.execute(
        """
        INSERT INTO dispensations
            (dispensation_number, dispense_type, patient_name, patient_phone, prescriber,
             prescription_number, location_id, payment_method, notes, user_id, receipt_token, customer_email,
             consent_whatsapp, consent_sms, consent_email)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (number, body["dispense_type"], body.get("patient_name"), body.get("patient_phone"),
         body.get("prescriber"), body.get("prescription_number"), location_id,
         body["payment_method"], body.get("notes"), user.id, token, body.get("customer_email"),
         bool(consent.get("whatsapp")), bool(consent.get("sms")), bool(consent.get("email"))),
    ).fetchone()
    dispensation_id = dispensation["id"]

    total = Decimal("0.00")
    reason = f"Dispensation {number}"
    for item in items:
        medicine = medicines[item["medicine_id"]]
        price = _money(item["unit_price"]) if item.get("unit_price") is not None else _money(medicine["selling_price"])
        line_total = _money(price * item["quantity"]) if price is not None else None
        line = conn.execute(
            """
            INSERT INTO dispensation_items (dispensation_id, medicine_id, quantity, unit_price, line_total, directions)
            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (dispensation_id, medicine["id"], item["quantity"], price, line_total, item.get("directions")),
        ).fetchone()
        stock.allocate_fefo(conn, user, medicine["id"], item["quantity"], location_id, reason, line["id"])
        total += line_total or 0

    subtotal = total
    discount = _money(body.get("discount_amount") or 0)
    if body.get("discount_percent"):
        discount = _money(subtotal * Decimal(str(body["discount_percent"])) / 100)
    if discount > subtotal:
        raise HTTPException(status_code=400, detail="The discount is larger than the sale")
    rate = Decimal(str(app_settings.get(conn, "receipt.tax_rate_percent") or 0))
    tax = _money((subtotal - discount) * rate / 100) if rate else Decimal("0.00")
    grand_total = subtotal - discount + tax
    conn.execute(
        """
        UPDATE dispensations SET total_amount = %s, subtotal_amount = %s, discount_amount = %s, tax_amount = %s,
               tax_label = %s WHERE id = %s
        """,
        (grand_total, subtotal, discount, tax, app_settings.get(conn, "receipt.tax_label") if rate else None,
         dispensation_id),
    )
    conn.execute(
        "INSERT INTO receipt_links (token, organization_id, dispensation_id, expires_at) "
        "VALUES (%s, current_org(), %s, CURRENT_TIMESTAMP + make_interval(days => %s))",
        (token, dispensation_id, RECEIPT_LINK_DAYS),
    )
    # Consent is asked at the counter and recorded per phone number and channel.
    for channel in ("whatsapp", "sms"):
        if consent.get(channel):
            conn.execute(
                """
                INSERT INTO message_consents (phone, channel, status, source, recorded_by)
                VALUES (%s, %s, 'OPTED_IN', 'COUNTER', %s)
                ON CONFLICT (organization_id, phone, channel) DO UPDATE
                    SET status = 'OPTED_IN', source = 'COUNTER', recorded_by = EXCLUDED.recorded_by,
                        recorded_at = CURRENT_TIMESTAMP
                """,
                (body["patient_phone"], channel.upper(), user.id),
            )

    audit.record(conn, user, "DISPENSE", "dispensation", dispensation_id, None, {
        "number": number, "type": body["dispense_type"], "payment_method": body["payment_method"],
        "subtotal": subtotal, "discount": discount, "tax": tax, "total": grand_total,
        "items": [{"medicine_id": i["medicine_id"], "quantity": i["quantity"]} for i in items],
    })
    return get(conn, dispensation_id)


def get(conn: psycopg.Connection, dispensation_id: int) -> dict:
    header = conn.execute(
        """
        SELECT d.*, users.full_name AS dispensed_by, voider.full_name AS voided_by_name,
               locations.name AS location
        FROM dispensations d
        JOIN users ON users.id = d.user_id
        LEFT JOIN users voider ON voider.id = d.voided_by
        LEFT JOIN locations ON locations.id = d.location_id
        WHERE d.id = %s
        """,
        (dispensation_id,),
    ).fetchone()
    if header is None:
        raise HTTPException(status_code=404, detail="Dispensation not found")

    lines = conn.execute(
        """
        SELECT di.id, di.medicine_id, medicines.name AS medicine, medicines.strength, medicines.dosage_form,
               di.quantity, di.unit_price, di.line_total, di.directions
        FROM dispensation_items di
        JOIN medicines ON medicines.id = di.medicine_id
        WHERE di.dispensation_id = %s
        ORDER BY di.id
        """,
        (dispensation_id,),
    ).fetchall()

    batches = conn.execute(
        """
        SELECT sm.dispensation_item_id, sm.batch_id, batches.batch_number, batches.expiry_date,
               sm.quantity, sm.movement_type
        FROM stock_movements sm
        JOIN batches ON batches.id = sm.batch_id
        JOIN dispensation_items di ON di.id = sm.dispensation_item_id
        WHERE di.dispensation_id = %s AND sm.movement_type = 'DISPENSED'
        ORDER BY sm.id
        """,
        (dispensation_id,),
    ).fetchall()
    for line in lines:
        line["batches"] = [b for b in batches if b["dispensation_item_id"] == line["id"]]

    return {**header, "items": lines}


def void(conn: psycopg.Connection, user: CurrentUser, dispensation_id: int, reason: str) -> dict:
    header = conn.execute(
        "SELECT id, dispensation_number, status FROM dispensations WHERE id = %s FOR UPDATE",
        (dispensation_id,),
    ).fetchone()
    if header is None:
        raise HTTPException(status_code=404, detail="Dispensation not found")
    if header["status"] == "VOIDED":
        raise HTTPException(status_code=400, detail="This dispensation is already voided")

    movements = conn.execute(
        """
        SELECT sm.batch_id, sm.quantity, sm.dispensation_item_id
        FROM stock_movements sm
        JOIN dispensation_items di ON di.id = sm.dispensation_item_id
        WHERE di.dispensation_id = %s AND sm.movement_type = 'DISPENSED'
        ORDER BY sm.batch_id
        FOR UPDATE OF sm
        """,
        (dispensation_id,),
    ).fetchall()

    note = f"Void of {header['dispensation_number']}: {reason}"
    for movement in movements:
        # Lock the batch, return the units, record the RETURNED movement.
        conn.execute("SELECT id FROM batches WHERE id = %s FOR UPDATE", (movement["batch_id"],))
        conn.execute("UPDATE batches SET quantity = quantity + %s WHERE id = %s",
                     (movement["quantity"], movement["batch_id"]))
        stock.insert_movement(conn, movement["batch_id"], "RETURNED", movement["quantity"], note, user,
                              movement["dispensation_item_id"])

    conn.execute(
        """
        UPDATE dispensations SET status = 'VOIDED', voided_at = CURRENT_TIMESTAMP, voided_by = %s,
               void_reason = %s
        WHERE id = %s
        """,
        (user.id, reason, dispensation_id),
    )
    audit.record(conn, user, "VOID", "dispensation", dispensation_id,
                 {"status": "COMPLETED"}, {"status": "VOIDED", "reason": reason,
                                           "units_returned": sum(m["quantity"] for m in movements)})
    return get(conn, dispensation_id)


def search(conn, *, date_from=None, date_to=None, status=None, search=None, limit=500) -> list[dict]:
    return conn.execute(
        """
        SELECT d.id, d.dispensation_number, d.dispensed_at, d.dispense_type, d.patient_name,
               d.prescription_number, d.prescriber, d.payment_method, d.total_amount, d.status,
               users.full_name AS dispensed_by,
               COUNT(di.id) AS lines, COALESCE(SUM(di.quantity), 0)::int AS units,
               STRING_AGG(medicines.name, ', ' ORDER BY di.id) AS medicines
        FROM dispensations d
        JOIN users ON users.id = d.user_id
        LEFT JOIN dispensation_items di ON di.dispensation_id = d.id
        LEFT JOIN medicines ON medicines.id = di.medicine_id
        WHERE (%(f)s::date IS NULL OR d.dispensed_at >= %(f)s::date)
          AND (%(t)s::date IS NULL OR d.dispensed_at < %(t)s::date + 1)
          AND (%(status)s::text IS NULL OR d.status = %(status)s::text)
          AND (%(q)s::text IS NULL OR d.dispensation_number ILIKE %(p)s OR d.patient_name ILIKE %(p)s
               OR d.prescription_number ILIKE %(p)s OR medicines.name ILIKE %(p)s)
        GROUP BY d.id, users.full_name
        ORDER BY d.dispensed_at DESC, d.id DESC
        LIMIT %(limit)s
        """,
        {"f": date_from, "t": date_to, "status": status, "q": search,
         "p": f"%{(search or '').strip()}%", "limit": limit},
    ).fetchall()


def summary(conn, day: date) -> dict:
    """Totals for one day (voided dispensations excluded from sales)."""
    rows = conn.execute(
        """
        SELECT payment_method, COUNT(*) AS dispensations, COALESCE(SUM(total_amount), 0) AS amount
        FROM dispensations
        WHERE dispensed_at >= %(d)s::date AND dispensed_at < %(d)s::date + 1 AND status = 'COMPLETED'
        GROUP BY payment_method ORDER BY payment_method
        """,
        {"d": day},
    ).fetchall()
    totals = conn.execute(
        """
        SELECT COUNT(DISTINCT d.id) FILTER (WHERE d.status = 'COMPLETED') AS dispensations,
               COUNT(DISTINCT d.id) FILTER (WHERE d.status = 'VOIDED') AS voided,
               COUNT(DISTINCT d.id) FILTER (WHERE d.status = 'COMPLETED' AND d.dispense_type = 'PRESCRIPTION') AS prescriptions,
               COALESCE(SUM(di.quantity) FILTER (WHERE d.status = 'COMPLETED'), 0)::int AS units
        FROM dispensations d
        LEFT JOIN dispensation_items di ON di.dispensation_id = d.id
        WHERE d.dispensed_at >= %(d)s::date AND d.dispensed_at < %(d)s::date + 1
        """,
        {"d": day},
    ).fetchone()
    top = conn.execute(
        """
        SELECT medicines.name AS medicine, medicines.strength, SUM(di.quantity)::int AS units
        FROM dispensation_items di
        JOIN dispensations d ON d.id = di.dispensation_id
        JOIN medicines ON medicines.id = di.medicine_id
        WHERE d.dispensed_at >= %(d)s::date AND d.dispensed_at < %(d)s::date + 1 AND d.status = 'COMPLETED'
        GROUP BY medicines.id ORDER BY units DESC LIMIT 5
        """,
        {"d": day},
    ).fetchall()
    return {
        "date": day,
        **totals,
        "sales_total": sum(float(r["amount"]) for r in rows),
        "by_payment_method": rows,
        "top_medicines": top,
    }
