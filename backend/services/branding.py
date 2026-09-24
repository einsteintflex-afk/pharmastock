# ============================================================
# COMPANY BRANDING AND RECEIPTS
# ============================================================
# Each organization's documents (receipts, PDF reports, messages) carry its
# own name, logo and details. MedCart Tech's attribution ("Powered by MedCart
# Tech") is a platform setting shown on customer-facing documents.
#
# Logo upload is treated as untrusted input: size-limited, opened with
# Pillow (decompression-bomb guard), accepted only as PNG / JPEG / WebP,
# re-encoded to a fresh PNG (drops metadata and any trailing payload),
# downscaled, and stored in the database (never served from a file path).

import base64
import hashlib
import html
import io

import psycopg
from fastapi import HTTPException

from . import app_settings

MAX_LOGO_BYTES = 2 * 1024 * 1024
MAX_LOGO_SIDE = 600
ALLOWED_FORMATS = {"PNG", "JPEG", "WEBP"}


def platform_setting(conn: psycopg.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM platform_settings WHERE key = %s", (key,)).fetchone()
    return row["value"] if row else default


def powered_by(conn: psycopg.Connection) -> str | None:
    if not platform_setting(conn, "branding.show_powered_by", True):
        return None
    return platform_setting(conn, "branding.powered_by", "Powered by MedCart Tech")


def profile(conn: psycopg.Connection, with_logo: bool = True) -> dict:
    """The current organization's branding (row level security)."""
    values = app_settings.get_all(conn)
    logo = conn.execute("SELECT content, sha256 FROM organization_files WHERE kind = 'LOGO'").fetchone()
    return {
        "name": values.get("pharmacy.name") or "",
        "address": values.get("pharmacy.address") or "",
        "phone": values.get("pharmacy.phone") or "",
        "email": values.get("company.email") or "",
        "website": values.get("company.website") or "",
        "registration_number": values.get("company.registration_number") or "",
        "tax_id": values.get("company.tax_id") or "",
        "receipt_footer": values.get("receipt.footer") or "",
        "report_footer": values.get("report.footer") or "",
        "currency": values.get("currency.symbol") or "",
        "show_batches": values.get("receipt.show_batches", True),
        "powered_by": powered_by(conn),
        "has_logo": logo is not None,
        "logo_version": logo["sha256"][:12] if logo else None,
        "logo_png": bytes(logo["content"]) if (logo and with_logo) else None,
    }


def validate_logo(data: bytes) -> tuple[bytes, int, int]:
    from PIL import Image, UnidentifiedImageError

    if not data:
        raise HTTPException(status_code=400, detail="The file is empty")
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(status_code=413, detail="The logo must be 2 MB or smaller")
    Image.MAX_IMAGE_PIXELS = 25_000_000
    try:
        with Image.open(io.BytesIO(data)) as probe:
            if probe.format not in ALLOWED_FORMATS:
                raise HTTPException(status_code=415, detail="Upload a PNG, JPEG or WebP image")
            probe.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            image = image.convert("RGBA")
            image.thumbnail((MAX_LOGO_SIDE, MAX_LOGO_SIDE))
            out = io.BytesIO()
            image.save(out, format="PNG", optimize=True)
            return out.getvalue(), image.width, image.height
    except HTTPException:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise HTTPException(status_code=415, detail="The file is not a valid image")


def save_logo(conn: psycopg.Connection, user_id: int, data: bytes) -> dict:
    png, width, height = validate_logo(data)
    digest = hashlib.sha256(png).hexdigest()
    conn.execute(
        """
        INSERT INTO organization_files (kind, content, content_type, size_bytes, sha256, width, height, uploaded_by)
        VALUES ('LOGO', %s, 'image/png', %s, %s, %s, %s, %s)
        ON CONFLICT (organization_id, kind) DO UPDATE SET content = EXCLUDED.content, size_bytes = EXCLUDED.size_bytes,
            sha256 = EXCLUDED.sha256, width = EXCLUDED.width, height = EXCLUDED.height,
            uploaded_by = EXCLUDED.uploaded_by, uploaded_at = CURRENT_TIMESTAMP
        """,
        (png, len(png), digest, width, height, user_id),
    )
    return {"size_bytes": len(png), "width": width, "height": height, "sha256": digest}


# ------------------------------------------------------------
# Receipts
# ------------------------------------------------------------

def _fmt(value, currency: str) -> str:
    return "—" if value is None else f"{currency}{float(value):,.2f}"


def receipt_html(brand: dict, sale: dict) -> str:
    """Self-contained, escaped HTML receipt for the public digital-receipt link."""
    e = html.escape
    cur = brand["currency"]
    logo = (f'<img class="logo" alt="" src="data:image/png;base64,{base64.b64encode(brand["logo_png"]).decode()}">'
            if brand.get("logo_png") else "")
    details = " · ".join(e(x) for x in (brand["address"], brand["phone"], brand["email"], brand["website"]) if x)
    ids = " · ".join(e(x) for x in (f"Reg. {brand['registration_number']}" if brand["registration_number"] else "",
                                     f"TIN {brand['tax_id']}" if brand["tax_id"] else "") if x)
    rows = []
    for item in sale["items"]:
        batches = ""
        if brand["show_batches"] and item.get("batches"):
            batches = "<div class='muted'>" + ", ".join(
                f"Batch {e(b['batch_number'])} exp {e(str(b['expiry_date']))}" for b in item["batches"]) + "</div>"
        rows.append(
            f"<tr><td>{e(item['medicine'])} {e(item.get('strength') or '')}{batches}"
            f"{'<div class=muted>' + e(item['directions']) + '</div>' if item.get('directions') else ''}</td>"
            f"<td class=num>{item['quantity']}</td><td class=num>{_fmt(item['unit_price'], cur)}</td>"
            f"<td class=num>{_fmt(item['line_total'], cur)}</td></tr>")
    totals = [("Subtotal", sale.get("subtotal_amount") or sale.get("total_amount"))]
    if sale.get("discount_amount"):
        totals.append(("Discount", -float(sale["discount_amount"])))
    if sale.get("tax_amount"):
        totals.append((sale.get("tax_label") or "Tax", sale["tax_amount"]))
    totals.append(("Total", sale["total_amount"]))
    total_rows = "".join(f"<tr class='{'grand' if label == 'Total' else ''}'><td colspan=3>{e(label)}</td>"
                         f"<td class=num>{_fmt(value, cur)}</td></tr>" for label, value in totals)
    void = "<p class=void>VOIDED</p>" if sale.get("status") == "VOIDED" else ""
    footer = f"<p class=footer>{e(brand['receipt_footer'])}</p>" if brand["receipt_footer"] else ""
    powered = f"<p class=powered>{e(brand['powered_by'])}</p>" if brand.get("powered_by") else ""
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Receipt {e(sale['dispensation_number'])} — {e(brand['name'])}</title>
<style>
body{{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#f4f6f9;color:#1b2430;margin:0;padding:16px}}
.r{{max-width:420px;margin:0 auto;background:#fff;border-radius:12px;padding:20px;box-shadow:0 2px 12px rgba(0,0,0,.08)}}
.logo{{max-height:64px;max-width:180px;display:block;margin:0 auto 8px}}
h1{{font-size:18px;text-align:center;margin:4px 0}} .c{{text-align:center}} .muted{{color:#5d6b7e;font-size:12px}}
table{{width:100%;border-collapse:collapse;margin-top:12px;font-size:14px}} td{{padding:6px 2px;border-bottom:1px solid #eef1f5;vertical-align:top}}
.num{{text-align:right;white-space:nowrap}} .grand td{{font-weight:700;font-size:16px;border-top:2px solid #1b2430}}
.void{{color:#b42318;font-weight:700;text-align:center;font-size:20px}} .footer{{text-align:center;margin-top:14px}}
.powered{{text-align:center;color:#8a96a8;font-size:11px;margin-top:10px}}
</style></head><body><main class="r">
{logo}<h1>{e(brand['name'])}</h1><p class="c muted">{details}</p>{f'<p class="c muted">{ids}</p>' if ids else ''}
{void}
<p class="muted">Receipt <strong>{e(sale['dispensation_number'])}</strong><br>{e(str(sale['dispensed_at'])[:16])}
 · {e(sale['payment_method'].replace('_', ' ').title())} · Served by {e(sale['dispensed_by'])}</p>
<table><tr class="muted"><td>Item</td><td class=num>Qty</td><td class=num>Price</td><td class=num>Amount</td></tr>
{''.join(rows)}{total_rows}</table>{footer}{powered}</main></body></html>"""


def receipt_pdf(brand: dict, sale: dict, layout: str = "a4") -> bytes:
    """PDF receipt: A4, or 80 mm thermal roll."""
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    thermal = layout == "thermal"
    width = 80 * mm if thermal else A4[0]
    lines = 30 + 3 * len(sale["items"])
    size = (width, max(120, lines * 6) * mm) if thermal else A4
    margin = 4 * mm if thermal else 15 * mm
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=size, leftMargin=margin, rightMargin=margin, topMargin=margin,
                            bottomMargin=margin, title=f"Receipt {sale['dispensation_number']}", author=brand["name"])
    styles = getSampleStyleSheet()
    base = 7.5 if thermal else 9.5
    normal = styles["BodyText"].clone("n", fontSize=base, leading=base + 2)
    center = normal.clone("c", alignment=1)
    title = styles["Title"].clone("t", fontSize=base + 4, leading=base + 6)
    e = html.escape
    story = []
    if brand.get("logo_png"):
        from PIL import Image as PILImage
        with PILImage.open(io.BytesIO(brand["logo_png"])) as probe:
            ratio = probe.height / probe.width
        logo_w = (30 if thermal else 40) * mm
        story.append(Image(io.BytesIO(brand["logo_png"]), width=logo_w, height=logo_w * ratio))
    story.append(Paragraph(e(brand["name"]), title))
    for text in (brand["address"], " · ".join(x for x in (brand["phone"], brand["email"]) if x), brand["website"],
                 " · ".join(x for x in (f"Reg. {brand['registration_number']}" if brand["registration_number"] else "",
                                        f"TIN {brand['tax_id']}" if brand["tax_id"] else "") if x)):
        if text:
            story.append(Paragraph(e(text), center))
    if sale.get("status") == "VOIDED":
        story.append(Paragraph("<b>VOIDED</b>", title))
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(f"Receipt <b>{e(sale['dispensation_number'])}</b> · {e(str(sale['dispensed_at'])[:16])}", normal))
    story.append(Paragraph(f"{e(sale['payment_method'].replace('_', ' ').title())} · Served by {e(sale['dispensed_by'])}",
                           normal))
    if sale.get("patient_name"):
        story.append(Paragraph(f"Customer: {e(sale['patient_name'])}", normal))
    cur = brand["currency"]
    data = [["Item", "Qty", "Price", "Amount"]]
    for item in sale["items"]:
        text = f"{e(item['medicine'])} {e(item.get('strength') or '')}"
        if brand["show_batches"] and item.get("batches"):
            text += "<br/><font size=6>" + ", ".join(
                f"{e(b['batch_number'])} exp {b['expiry_date']}" for b in item["batches"]) + "</font>"
        data.append([Paragraph(text, normal), str(item["quantity"]), _fmt(item["unit_price"], cur),
                     _fmt(item["line_total"], cur)])
    data.append(["Subtotal", "", "", _fmt(sale.get("subtotal_amount") or sale.get("total_amount"), cur)])
    if sale.get("discount_amount"):
        data.append(["Discount", "", "", "-" + _fmt(sale["discount_amount"], cur)])
    if sale.get("tax_amount"):
        data.append([sale.get("tax_label") or "Tax", "", "", _fmt(sale["tax_amount"], cur)])
    data.append(["TOTAL", "", "", _fmt(sale["total_amount"], cur)])
    inner = width - 2 * margin
    table = Table(data, colWidths=[inner * 0.46, inner * 0.12, inner * 0.2, inner * 0.22], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), base), ("ALIGN", (1, 0), (-1, -1), "RIGHT"),
        ("LINEBELOW", (0, 0), (-1, 0), 0.5, colors.black), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEABOVE", (0, -1), (-1, -1), 1, colors.black), ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
    ]))
    story += [Spacer(1, 2 * mm), table, Spacer(1, 4 * mm)]
    if brand["receipt_footer"]:
        story.append(Paragraph(e(brand["receipt_footer"]), center))
    if brand.get("powered_by"):
        story.append(Paragraph(f"<font color='#8a96a8' size=6>{e(brand['powered_by'])}</font>", center))
    doc.build(story)
    return buffer.getvalue()
