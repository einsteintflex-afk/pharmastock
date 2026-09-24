# ============================================================
# REPORTS AND EXPORT
# ============================================================

from datetime import date
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from .. import audit
from ..database import get_db
from ..security import CurrentUser, require
from ..services import app_settings, branding, exporters, reports

router = APIRouter(tags=["Reports"])

MEDIA_TYPES = {
    "csv": ("text/csv; charset=utf-8", "csv"),
    "xlsx": ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
    "pdf": ("application/pdf", "pdf"),
}


@router.get("/reports")
def list_reports(user: CurrentUser = Depends(require("analytics.read"))):
    return [
        {"key": key, "name": name, "filters": filters, "formats": ["json", "csv", "xlsx", "pdf"]}
        for key, (name, _, filters) in reports.REPORTS.items()
        if user.can(reports.REPORT_PERMISSIONS.get(key, "analytics.read"))
    ]


@router.get("/reports/{report_key}")
def run_report(
    report_key: str,
    format: Literal["json", "csv", "xlsx", "pdf"] = "json",
    date_from: date | None = None,
    date_to: date | None = None,
    location_id: int | None = None,
    supplier_id: int | None = None,
    status: Literal["EXPIRED", "CRITICAL", "URGENT", "APPROACHING EXPIRY", "NORMAL"] | None = None,
    movement_type: Literal["RECEIVED", "DISPENSED", "RETURNED", "DAMAGED", "EXPIRED", "ADJUSTMENT",
                           "TRANSFER_OUT", "TRANSFER_IN"] | None = None,
    user: CurrentUser = Depends(require("analytics.read")),
    conn: psycopg.Connection = Depends(get_db),
):
    if report_key not in reports.REPORTS:
        raise HTTPException(status_code=404, detail="Unknown report")
    needed = reports.REPORT_PERMISSIONS.get(report_key)
    if needed and not user.can(needed):
        raise HTTPException(status_code=403, detail=f"This report needs the {needed} permission.")
    if format != "json" and not user.can("reports.export"):
        raise HTTPException(status_code=403, detail="Your role does not allow exporting reports (reports.export).")

    currency = str(app_settings.get(conn, "currency.symbol"))
    report = reports.build(
        conn, report_key, currency, date_from=date_from, date_to=date_to, location_id=location_id,
        supplier_id=supplier_id, status=status, movement_type=movement_type,
    )

    filters = {k: str(v) for k, v in {"date_from": date_from, "date_to": date_to, "location_id": location_id,
                                      "supplier_id": supplier_id, "status": status,
                                      "movement_type": movement_type}.items() if v is not None}
    if format == "json":
        audit.record(conn, user, "VIEW_REPORT", "report", report_key, None,
                     {"rows": len(report.rows), "filters": filters})
        conn.commit()
        return exporters.to_json(report)

    if format == "csv":
        content = exporters.to_csv(report)
    elif format == "xlsx":
        content = exporters.to_xlsx(report, user.full_name)
    else:
        content = exporters.to_pdf(report, user.full_name, branding.profile(conn))

    audit.record(conn, user, "EXPORT", "report", report_key, None,
                 {"format": format, "rows": len(report.rows), "filters": filters})
    conn.commit()

    media_type, extension = MEDIA_TYPES[format]
    filename = f"pharmastock-{report_key}-{date.today():%Y%m%d}.{extension}"
    return Response(
        content=content, media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
