# ============================================================
# SCHEDULED REPORTS
# ============================================================
# A scheduled report runs a normal report (services/reports.py) at a set
# time, exports it (CSV / Excel / PDF) and e-mails it to its recipients
# through the delivery outbox. Times are the server's local time.

from datetime import date, datetime, timedelta

import psycopg
from fastapi import HTTPException

from .. import audit
from . import app_settings, branding, delivery, exporters, reports

MEDIA = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
}
ALLOWED_FILTERS = {"date_from", "date_to", "location_id", "supplier_id", "status", "movement_type",
                   "period_days"}


def next_run(frequency: str, hour: int, day_of_week: int | None, day_of_month: int | None,
             after: datetime) -> datetime:
    """First run time strictly after `after`."""
    candidate = after.replace(hour=hour, minute=0, second=0, microsecond=0)
    if frequency == "DAILY":
        if candidate <= after:
            candidate += timedelta(days=1)
        return candidate
    if frequency == "WEEKLY":
        weekday = 0 if day_of_week is None else day_of_week
        candidate += timedelta(days=(weekday - candidate.weekday()) % 7)
        if candidate <= after:
            candidate += timedelta(days=7)
        return candidate
    # MONTHLY
    day = day_of_month or 1
    candidate = candidate.replace(day=day)
    if candidate <= after:
        month = candidate.month % 12 + 1
        year = candidate.year + (1 if month == 1 else 0)
        candidate = candidate.replace(year=year, month=month)
    return candidate


def validate(body: dict) -> None:
    if body["report_key"] not in reports.REPORTS:
        raise HTTPException(status_code=400, detail="Unknown report")
    if body["frequency"] == "WEEKLY" and body.get("day_of_week") is None:
        raise HTTPException(status_code=400, detail="Weekly schedules need day_of_week (0 = Monday)")
    if body["frequency"] == "MONTHLY" and body.get("day_of_month") is None:
        raise HTTPException(status_code=400, detail="Monthly schedules need day_of_month (1-28)")
    unknown = set(body.get("filters") or {}) - ALLOWED_FILTERS
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown filters: {sorted(unknown)}")


def _filters(saved: dict) -> dict:
    """Stored filters -> report arguments. period_days makes date ranges
    rolling (e.g. the last 7 days on every run)."""
    filters = dict(saved or {})
    period = filters.pop("period_days", None)
    if period:
        filters["date_to"] = date.today() - timedelta(days=1)
        filters["date_from"] = filters["date_to"] - timedelta(days=int(period) - 1)
    for key in ("date_from", "date_to"):
        if isinstance(filters.get(key), str):
            filters[key] = date.fromisoformat(filters[key])
    return filters


def run(conn: psycopg.Connection, schedule: dict, user=None) -> dict:
    """Build the report, queue one e-mail per recipient, record the run."""
    currency = str(app_settings.get(conn, "currency.symbol"))
    report = reports.build(conn, schedule["report_key"], currency, **_filters(schedule["filters"]))
    fmt = schedule["format"]
    author = user.full_name if user else "PharmaStock scheduler"
    content = {"csv": lambda: exporters.to_csv(report), "xlsx": lambda: exporters.to_xlsx(report, author),
               "pdf": lambda: exporters.to_pdf(report, author, branding.profile(conn))}[fmt]()
    filename = f"pharmastock-{schedule['report_key']}-{date.today():%Y%m%d}.{fmt}"
    body = (f"{report.title}\n{report.subtitle}\n\n"
            + "\n".join(f"{label}: {value}" for label, value in report.summary)
            + f"\n\nRows: {len(report.rows)}. The full report is attached ({fmt.upper()}).\n\n"
              f"Scheduled report \"{schedule['name']}\" ({schedule['frequency'].lower()}).")
    for recipient in schedule["recipients"]:
        delivery.enqueue(conn, channel="EMAIL", recipient=recipient,
                         subject=f"[PharmaStock] {schedule['name']} - {date.today():%d %b %Y}", body=body,
                         scheduled_report_id=schedule["id"], attachment=(filename, MEDIA[fmt], content))
    status = f"Queued to {len(schedule['recipients'])} recipient(s), {len(report.rows)} rows"
    conn.execute(
        "UPDATE scheduled_reports SET last_run_at = CURRENT_TIMESTAMP, last_status = %s WHERE id = %s",
        (status, schedule["id"]),
    )
    audit.record(conn, user, "RUN_SCHEDULED_REPORT", "scheduled_report", schedule["id"], None,
                 {"report": schedule["report_key"], "format": fmt, "rows": len(report.rows),
                  "recipients": len(schedule["recipients"])},
                 username=None if user else "scheduler")
    return {"status": status, "rows": len(report.rows)}


def run_due(conn: psycopg.Connection) -> int:
    """Run every due schedule of the current organization; returns the count."""
    count = 0
    conn.commit()  # fresh transaction: LOCALTIMESTAMP is "now"
    while True:
        schedule = conn.execute(
            """
            SELECT * FROM scheduled_reports
            WHERE is_active AND next_run_at <= LOCALTIMESTAMP
            ORDER BY next_run_at LIMIT 1
            FOR UPDATE SKIP LOCKED
            """
        ).fetchone()
        if schedule is None:
            conn.commit()
            return count
        now = conn.execute("SELECT LOCALTIMESTAMP::timestamp AS now").fetchone()["now"]
        following = next_run(schedule["frequency"], schedule["hour"], schedule["day_of_week"],
                             schedule["day_of_month"], now)
        try:
            with conn.transaction():
                run(conn, schedule)
        except Exception as error:  # noqa: BLE001 - record and move on
            conn.execute("UPDATE scheduled_reports SET last_status = %s, last_run_at = CURRENT_TIMESTAMP "
                         "WHERE id = %s", (f"Failed: {error}"[:500], schedule["id"]))
        conn.execute("UPDATE scheduled_reports SET next_run_at = %s WHERE id = %s", (following, schedule["id"]))
        conn.commit()
        count += 1

