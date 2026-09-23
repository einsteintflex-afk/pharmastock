# ============================================================
# CONFIGURATION
# ============================================================
# All configuration comes from environment variables (loaded from .env in
# development). Secrets are never hard-coded.

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    database_url: str
    environment: str
    session_hours: int
    cookie_secure: bool
    max_failed_logins: int
    lockout_minutes: int
    db_pool_min: int
    db_pool_max: int
    notification_refresh_minutes: int
    anthropic_api_key: str | None
    anthropic_model: str
    bootstrap_admin_username: str | None
    bootstrap_admin_password: str | None
    log_level: str
    frontend_dir: Path
    # Outgoing e-mail (SMTP). Delivery is disabled while smtp_host is empty.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    smtp_security: str = "starttls"   # starttls | ssl | none
    # SMS: "none", "log" (development: write to the log) or "webhook"
    # (POST JSON {to, message} to SMS_WEBHOOK_URL - adapt to any gateway).
    sms_provider: str = "none"
    sms_webhook_url: str | None = None
    sms_webhook_token: str | None = None
    delivery_interval_seconds: int = 60
    app_base_url: str = ""

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


def load_settings() -> Settings:
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL is not set in the .env file")

    environment = os.getenv("PHARMASTOCK_ENV", "development").lower()

    return Settings(
        database_url=database_url,
        environment=environment,
        session_hours=_int("SESSION_HOURS", 12),
        # Secure cookies require HTTPS; default on in production only.
        cookie_secure=_bool("COOKIE_SECURE", environment == "production"),
        max_failed_logins=_int("MAX_FAILED_LOGINS", 5),
        lockout_minutes=_int("LOCKOUT_MINUTES", 15),
        db_pool_min=_int("DB_POOL_MIN", 1),
        db_pool_max=_int("DB_POOL_MAX", 10),
        notification_refresh_minutes=_int("NOTIFICATION_REFRESH_MINUTES", 15),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY") or None,
        anthropic_model=os.getenv("ANTHROPIC_MODEL", "claude-opus-5"),
        bootstrap_admin_username=os.getenv("BOOTSTRAP_ADMIN_USERNAME") or None,
        bootstrap_admin_password=os.getenv("BOOTSTRAP_ADMIN_PASSWORD") or None,
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        frontend_dir=PROJECT_ROOT / "frontend",
        smtp_host=os.getenv("SMTP_HOST") or None,
        smtp_port=_int("SMTP_PORT", 587),
        smtp_username=os.getenv("SMTP_USERNAME") or None,
        smtp_password=os.getenv("SMTP_PASSWORD") or None,
        smtp_from=os.getenv("SMTP_FROM") or None,
        smtp_security=os.getenv("SMTP_SECURITY", "starttls").lower(),
        sms_provider=os.getenv("SMS_PROVIDER", "none").lower(),
        sms_webhook_url=os.getenv("SMS_WEBHOOK_URL") or None,
        sms_webhook_token=os.getenv("SMS_WEBHOOK_TOKEN") or None,
        delivery_interval_seconds=_int("DELIVERY_INTERVAL_SECONDS", 60),
        app_base_url=os.getenv("APP_BASE_URL", "").rstrip("/"),
    )


settings = load_settings()
