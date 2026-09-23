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
    "pharmacy.name": (str, 1, 150),
    "pharmacy.address": (str, 0, 255),
    "pharmacy.phone": (str, 0, 50),
}


# Defaults for a new organization (the original installation received the
# same values through migrations 0006 and 0007).
DEFAULTS = {
    "expiry.critical_days": (30, "Batches expiring within this many days are CRITICAL"),
    "expiry.urgent_days": (90, "Batches expiring within this many days are URGENT"),
    "expiry.approaching_days": (180, "Batches expiring within this many days are APPROACHING EXPIRY"),
    "stock.slow_moving_units_90d": (10, "Medicines dispensing this many units or fewer in 90 days are SLOW-MOVING"),
    "reorder.lead_time_days": (14, "Typical supplier lead time used for reorder recommendations"),
    "reorder.cover_days": (30, "Days of stock a reorder should cover after it arrives"),
    "currency.symbol": ("₵", "Currency symbol shown in the interface and reports"),
    "pharmacy.name": ("PharmaStock Pharmacy", "Pharmacy name printed on receipts"),
    "pharmacy.address": ("", "Address printed on receipts"),
    "pharmacy.phone": ("", "Phone number printed on receipts"),
}


def seed_defaults(conn: psycopg.Connection, overrides: dict | None = None) -> None:
    """Insert any missing settings for the current organization."""
    import json

    for key, (value, description) in DEFAULTS.items():
        value = (overrides or {}).get(key, value)
        conn.execute(
            "INSERT INTO app_settings (key, value, description) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
            (key, json.dumps(value), description),
        )


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
