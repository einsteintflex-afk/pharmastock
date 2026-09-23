# ============================================================
# ORGANISATION SETTINGS
# ============================================================

from dataclasses import dataclass

import psycopg
from fastapi import HTTPException

# key -> (type, minimum, maximum)
SETTING_RULES = {
    "expiry.critical_days": (int, 1, 3650),
    "expiry.urgent_days": (int, 1, 3650),
    "expiry.approaching_days": (int, 1, 3650),
    "stock.slow_moving_units_90d": (int, 0, 1_000_000),
    "reorder.lead_time_days": (int, 0, 365),
    "reorder.cover_days": (int, 1, 365),
    "currency.symbol": (str, 1, 5),
}


@dataclass(frozen=True)
class ExpiryThresholds:
    critical_days: int
    urgent_days: int
    approaching_days: int

    def as_params(self) -> dict:
        return {
            "critical_days": self.critical_days,
            "urgent_days": self.urgent_days,
            "approaching_days": self.approaching_days,
        }


def get_all(conn: psycopg.Connection) -> dict:
    rows = conn.execute("SELECT key, value FROM app_settings").fetchall()
    return {row["key"]: row["value"] for row in rows}


def get(conn: psycopg.Connection, key: str):
    row = conn.execute("SELECT value FROM app_settings WHERE key = %s", (key,)).fetchone()
    if row is None:
        raise KeyError(key)
    return row["value"]


def expiry_thresholds(conn: psycopg.Connection) -> ExpiryThresholds:
    values = get_all(conn)
    return ExpiryThresholds(
        critical_days=int(values["expiry.critical_days"]),
        urgent_days=int(values["expiry.urgent_days"]),
        approaching_days=int(values["expiry.approaching_days"]),
    )


def validate(changes: dict, current: dict) -> dict:
    cleaned = {}
    for key, value in changes.items():
        if key not in SETTING_RULES:
            raise HTTPException(status_code=400, detail=f"Unknown setting: {key}")
        kind, low, high = SETTING_RULES[key]
        if kind is int:
            if isinstance(value, bool) or not isinstance(value, int):
                raise HTTPException(status_code=400, detail=f"{key} must be a whole number")
            if not low <= value <= high:
                raise HTTPException(status_code=400, detail=f"{key} must be between {low} and {high}")
        else:
            if not isinstance(value, str) or not low <= len(value.strip()) <= high:
                raise HTTPException(status_code=400, detail=f"{key} must be {low}-{high} characters")
            value = value.strip()
        cleaned[key] = value

    merged = {**current, **cleaned}
    critical = int(merged["expiry.critical_days"])
    urgent = int(merged["expiry.urgent_days"])
    approaching = int(merged["expiry.approaching_days"])
    if not critical < urgent < approaching:
        raise HTTPException(
            status_code=400,
            detail="Expiry thresholds must satisfy critical < urgent < approaching",
        )
    return cleaned
