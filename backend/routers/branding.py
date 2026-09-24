# ============================================================
# BRANDING, RECEIPTS (PDF / THERMAL / DIGITAL LINK)
# ============================================================

from datetime import datetime
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from .. import audit
from ..config import settings
from ..database import get_db, set_organization
from ..security import CurrentUser, get_current_user, require
from ..services import branding, dispensing

router = APIRouter(tags=["Branding and receipts"])

_PUBLIC_RECEIPT_CSP = ("default-src 'none'; img-src data:; style-src 'unsafe-inline'; frame-ancestors 'none'; "
                       "base-uri 'none'; form-action 'none'")


@router.get("/branding")
def get_branding(user: CurrentUser = Depends(get_current_user), conn: psycopg.Connection = Depends(get_db)):
    profile = branding.profile(conn, with_logo=False)
    profile.pop("logo_png")
    return profile


@router.get("/branding/logo")
def get_logo(user: CurrentUser = Depends(get_current_user), conn: psycopg.Connection = Depends(get_db)):
    row = conn.execute("SELECT content, sha256 FROM organization_files WHERE kind = 'LOGO'").fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="No logo uploaded")
    return Response(bytes(row["content"]), media_type="image/png",
                    headers={"Cache-Control": "private, max-age=300", "ETag": f'"{row["sha256"][:16]}"'})


@router.put("/branding/logo")
async def upload_logo(request: Request, user: CurrentUser = Depends(require("settings.manage")),
                      conn: psycopg.Connection = Depends(get_db)):
    """Body: the image file itself (Content-Type image/png, image/jpeg or image/webp)."""
    declared = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if declared not in ("image/png", "image/jpeg", "image/webp"):
        raise HTTPException(status_code=415, detail="Upload a PNG, JPEG or WebP image")
    length = int(request.headers.get("content-length") or 0)
    if length > branding.MAX_LOGO_BYTES:
        raise HTTPException(status_code=413, detail="The logo must be 2 MB or smaller")
    data = await request.body()
    import asyncio

    def store():
        result = branding.save_logo(conn, user.id, data)
        audit.record(conn, user, "LOGO_UPLOADED", "organization", user.organization_id, None,
                     {k: result[k] for k in ("size_bytes", "width", "height", "sha256")})
        conn.commit()
        return result

    return await asyncio.to_thread(store)


@router.delete("/branding/logo")
def delete_logo(user: CurrentUser = Depends(require("settings.manage")), conn: psycopg.Connection = Depends(get_db)):
    if conn.execute("DELETE FROM organization_files WHERE kind = 'LOGO' RETURNING id").fetchone() is None:
        raise HTTPException(status_code=404, detail="No logo uploaded")
    audit.record(conn, user, "LOGO_REMOVED", "organization", user.organization_id)
    conn.commit()
    return {"removed": True}


# ------------------------------------------------------------
# Receipts
# ------------------------------------------------------------

@router.get("/dispensations/{dispensation_id}/receipt.pdf")
def receipt_pdf(dispensation_id: int, layout: Literal["a4", "thermal"] = "a4",
                user: CurrentUser = Depends(require("inventory.read")), conn: psycopg.Connection = Depends(get_db)):
    sale = dispensing.get(conn, dispensation_id)
    content = branding.receipt_pdf(branding.profile(conn), sale, layout)
    return Response(content, media_type="application/pdf", headers={
        "Content-Disposition": f'inline; filename="receipt-{sale["dispensation_number"]}.pdf"'})


def receipt_url(request: Request, token: str) -> str:
    base = settings.app_base_url or str(request.base_url).rstrip("/")
    return f"{base}/r/{token}"


@router.get("/dispensations/{dispensation_id}/receipt-link")
def receipt_link(dispensation_id: int, request: Request, user: CurrentUser = Depends(require("inventory.read")),
                 conn: psycopg.Connection = Depends(get_db)):
    sale = conn.execute("SELECT receipt_token FROM dispensations WHERE id = %s", (dispensation_id,)).fetchone()
    if sale is None:
        raise HTTPException(status_code=404, detail="Dispensation not found")
    link = conn.execute("SELECT token, expires_at FROM receipt_links WHERE token = %s AND organization_id = %s",
                        (sale["receipt_token"], user.organization_id)).fetchone() if sale["receipt_token"] else None
    if link is None:
        raise HTTPException(status_code=404, detail="This sale has no digital receipt (recorded before digital receipts)")
    return {"url": receipt_url(request, link["token"]), "expires_at": link["expires_at"]}


@router.get("/r/{token}", include_in_schema=False)
def public_receipt(token: str, conn: psycopg.Connection = Depends(get_db)):
    """Digital receipt for the customer: unguessable 128-bit token, expires."""
    missing = HTMLResponse("<!DOCTYPE html><title>Receipt</title><p>This receipt link is invalid or has expired.</p>",
                           status_code=404, headers={"Content-Security-Policy": _PUBLIC_RECEIPT_CSP})
    if len(token) != 32 or not all(c in "0123456789abcdef" for c in token):
        return missing
    link = conn.execute("SELECT organization_id, dispensation_id, expires_at FROM receipt_links WHERE token = %s",
                        (token,)).fetchone()
    if link is None or link["expires_at"] < datetime.now():
        return missing
    set_organization(conn, link["organization_id"])
    sale = dispensing.get(conn, link["dispensation_id"])
    page = branding.receipt_html(branding.profile(conn), sale)
    return HTMLResponse(page, headers={"Content-Security-Policy": _PUBLIC_RECEIPT_CSP,
                                       "X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer"})
