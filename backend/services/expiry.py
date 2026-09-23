# ============================================================
# EXPIRY ENGINE
# ============================================================
# The single definition of expiry status, used by every endpoint, report,
# notification and the AI assistant. Thresholds come from app_settings.
#
#   days_remaining = expiry_date - today
#   EXPIRED             days_remaining < 0
#   CRITICAL            0 <= days_remaining <= critical_days
#   URGENT              days_remaining <= urgent_days
#   APPROACHING EXPIRY  days_remaining <= approaching_days
#   NORMAL              otherwise
#
# A batch is usable (dispensable) on its expiry date and unusable after it.

from datetime import date

from .app_settings import ExpiryThresholds

EXPIRED = "EXPIRED"
CRITICAL = "CRITICAL"
URGENT = "URGENT"
APPROACHING = "APPROACHING EXPIRY"
NORMAL = "NORMAL"

STATUS_ORDER = [EXPIRED, CRITICAL, URGENT, APPROACHING, NORMAL]


def status_sql(column: str = "batches.expiry_date") -> str:
    """SQL CASE expression. Requires the named parameters from
    ExpiryThresholds.as_params() in the query."""
    return f"""
        CASE
            WHEN {column} < CURRENT_DATE THEN 'EXPIRED'
            WHEN {column} - CURRENT_DATE <= %(critical_days)s THEN 'CRITICAL'
            WHEN {column} - CURRENT_DATE <= %(urgent_days)s THEN 'URGENT'
            WHEN {column} - CURRENT_DATE <= %(approaching_days)s THEN 'APPROACHING EXPIRY'
            ELSE 'NORMAL'
        END
    """


def classify(expiry_date: date, thresholds: ExpiryThresholds, today: date | None = None) -> tuple[int, str]:
    today = today or date.today()
    days = (expiry_date - today).days
    if days < 0:
        return days, EXPIRED
    if days <= thresholds.critical_days:
        return days, CRITICAL
    if days <= thresholds.urgent_days:
        return days, URGENT
    if days <= thresholds.approaching_days:
        return days, APPROACHING
    return days, NORMAL
