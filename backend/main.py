# ============================================================
# PHARMASTOCK API
# ============================================================
# Run from the project root:
#     python -m backend.migrate          (apply database migrations)
#     uvicorn backend.main:app --reload
# The frontend is served at /app.

import asyncio
import hashlib
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from . import database, observability, security_events
from .config import settings
from .migrate import pending_migrations
from .ratelimit import SlidingWindow
from .security import SESSION_COOKIE, client_ip, request_token
from .routers import (
    admin, analytics, assistant, auth, barcode, billing, branding, delivery, dispensing, inventory, medicines, messaging,
    organizations, platform, purchasing, reports, stock, stock_control, suppliers, transfers, users,
)
from .services import billing as billing_service
from .services import delivery as delivery_service
from .services import notifications, scheduler

VERSION = "2.0.0"

observability.configure_logging(settings.log_level, settings.log_format)
logger = logging.getLogger("pharmastock")
ERROR_REPORTING = observability.configure_error_reporting(settings.sentry_dsn, settings.error_webhook_url,
                                                          settings.environment)
auth_limiter = SlidingWindow(settings.rate_limit_auth_per_minute)
api_limiter = SlidingWindow(settings.rate_limit_api_per_minute)
AUTH_LIMITED_PATHS = {"/auth/login", "/auth/forgot-password", "/auth/reset-password"}
UNLIMITED_PREFIXES = ("/app", "/health", "/ready", "/metrics")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


# ============================================================
# STARTUP / SHUTDOWN
# ============================================================

def _check_migrations() -> None:
    with database.connect() as conn:
        pending = pending_migrations(conn)
    if pending:
        raise RuntimeError(
            "Database migrations are pending: " + ", ".join(pending)
            + ". Back up the database, then run: python -m backend.migrate"
        )


def _bootstrap_admin() -> None:
    """Create the first administrator from BOOTSTRAP_ADMIN_* variables, only
    when no user exists yet. The password must be changed at first sign-in."""
    from .security import hash_password, validate_password_strength

    with database.connect() as conn:
        if conn.execute("SELECT 1 FROM users LIMIT 1").fetchone():
            return
        username = settings.bootstrap_admin_username
        password = settings.bootstrap_admin_password
        if not username or not password:
            logger.warning(
                "No user accounts exist. Create an administrator with: "
                "python -m backend.manage create-admin"
            )
            return
        try:
            validate_password_strength(password, username)
        except HTTPException as error:
            raise RuntimeError(f"BOOTSTRAP_ADMIN_PASSWORD rejected: {error.detail}") from error
        # The first administrator belongs to the original organization and may
        # manage further organizations.
        database.set_organization(conn, 1)
        conn.execute(
            """
            INSERT INTO users (username, full_name, role, password_hash, must_change_password,
                               organization_id, is_platform_admin)
            VALUES (%s, 'Administrator', 'ADMINISTRATOR', %s, true, 1, true)
            """,
            (username, hash_password(password)),
        )
        conn.execute(
            "INSERT INTO audit_log (username, action, entity_type, new_value) VALUES (%s, 'BOOTSTRAP_ADMIN', 'user', %s)",
            ("system", json.dumps({"username": username})),
        )
        conn.commit()
        logger.info("Bootstrap administrator '%s' created", username)


def _refresh_notifications() -> None:
    """Recompute expiry / stock notifications for every active organization."""
    try:
        with database.pool.connection() as conn:
            organization_ids = database.active_organization_ids(conn)
            expired = billing_service.expire_due(conn)
            conn.commit()
        if expired:
            logger.info("Subscriptions / trials ended: %s organization(s)", expired)
        for organization_id in organization_ids:
            with database.organization_connection(organization_id) as conn:
                result = notifications.refresh(conn)
                conn.commit()
            logger.info("Notifications refreshed org=%s: %s", organization_id, result)
    except Exception:
        logger.exception("Notification refresh failed")


def _process_deliveries() -> None:
    """Run due scheduled reports and send queued e-mail / SMS, per organization."""
    try:
        with database.pool.connection() as conn:
            organization_ids = database.active_organization_ids(conn)
        for organization_id in organization_ids:
            with database.organization_connection(organization_id) as conn:
                ran = scheduler.run_due(conn)
                sent = delivery_service.process_outbox(conn)
            if ran or any(sent.values()):
                logger.info("Deliveries org=%s: scheduled reports run=%s, outbox=%s", organization_id, ran, sent)
    except Exception:
        logger.exception("Delivery worker failed")


def _check_secret_key() -> None:
    if settings.secret_key and len(settings.secret_key) >= 32:
        return
    message = ("SECRET_KEY is not set (or shorter than 32 characters). It encrypts stored secrets such as "
               "two-step verification keys and messaging provider credentials. Generate one with: "
               "python -c \"import secrets; print(secrets.token_urlsafe(48))\"")
    if settings.is_production:
        raise RuntimeError(message)
    logger.warning(message + " (development: a key derived from DATABASE_URL is used)")


def _check_database_role() -> None:
    """Row level security does not apply to superusers / BYPASSRLS roles."""
    with database.connect() as conn:
        bypass = database.role_bypasses_rls(conn)
    if not bypass:
        return
    message = ("The database role in DATABASE_URL is a superuser or has BYPASSRLS, so PostgreSQL "
               "row level security (organization isolation) is NOT enforced. Connect as the "
               "application role created by deploy/create_app_role.sql.")
    if settings.is_production:
        raise RuntimeError(message)
    logger.warning(message)


async def _notification_loop() -> None:
    while True:
        await asyncio.to_thread(_refresh_notifications)
        await asyncio.sleep(settings.notification_refresh_minutes * 60)


async def _delivery_loop() -> None:
    while True:
        await asyncio.sleep(settings.delivery_interval_seconds)
        await asyncio.to_thread(_process_deliveries)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_migrations()
    _check_secret_key()
    _check_database_role()
    _bootstrap_admin()
    database.open_pool()
    tasks = [asyncio.create_task(_notification_loop()), asyncio.create_task(_delivery_loop())]
    logger.info("PharmaStock API %s started (%s)", VERSION, settings.environment)
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        database.close_pool()


# ============================================================
# APPLICATION
# ============================================================

app = FastAPI(
    title="PharmaStock API",
    description="Pharmacy inventory management and stock intelligence system",
    version=VERSION,
    lifespan=lifespan,
    # Interactive API docs are for development only.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)


def _blocked_by_origin(request: Request) -> bool:
    """State-changing requests from another web origin are refused. The
    session cookie is SameSite=Strict already; this is defence in depth."""
    origin = request.headers.get("origin")
    if not origin:
        return False
    if origin.rstrip("/") in settings.allowed_origins:
        return False
    return urlparse(origin).netloc != request.headers.get("host", "")


def _guard(request: Request) -> JSONResponse | None:
    path = request.scope["path"]
    # Rate limits.
    if path in AUTH_LIMITED_PATHS and request.method == "POST":
        wait = auth_limiter.hit(f"{client_ip(request)}")
    elif not path.startswith(UNLIMITED_PREFIXES) and path != "/":
        token = request_token(request)
        key = f"t:{hashlib.sha256(token.encode()).hexdigest()[:24]}" if token else f"ip:{client_ip(request)}"
        wait = api_limiter.hit(key)
    else:
        wait = None
    if wait is not None:
        return JSONResponse(status_code=429, headers={"Retry-After": str(int(wait) + 1)},
                            content={"detail": "Too many requests. Please wait a moment and try again."})
    # CSRF: other origins may not change state; cookie-authenticated
    # requests must carry the application's custom header (a cross-site form
    # or simple request cannot set it).
    if request.method in UNSAFE_METHODS:
        if _blocked_by_origin(request):
            return JSONResponse(status_code=403, content={"detail": "Cross-origin request refused"})
        uses_cookie = SESSION_COOKIE in request.cookies and not request.headers.get("authorization")
        if uses_cookie and request.headers.get("x-requested-with") != "PharmaStock":
            return JSONResponse(status_code=403, content={"detail": "Missing request header (CSRF protection)"})
    return None


async def _security_event(request: Request, status: int) -> None:
    path = request.scope["path"]
    if status < 400 or path.startswith(UNLIMITED_PREFIXES):
        return
    user = getattr(request.state, "user", None)
    if path == "/auth/login" and request.method == "POST" and status in (401, 423):
        event = "LOGIN_FAILED" if status == 401 else "LOGIN_LOCKED"
    elif status == 403 and request.method in UNSAFE_METHODS and user is None \
            and request.headers.get("x-requested-with") != "PharmaStock":
        event = "CSRF_REFUSED"
    else:
        event = security_events.classify(request.method, path, status, bool(request_token(request)))
    if event is None:
        return
    await asyncio.to_thread(
        security_events.record, event,
        organization_id=user.organization_id if user else None, user_id=user.id if user else None,
        username=user.username if user else None, method=request.method, path=path, status_code=status,
        ip=client_ip(request), request_id=getattr(request.state, "request_id", None),
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    token = observability.request_id_var.set(request_id)
    started = time.perf_counter()

    # Versioned base path for mobile and integration clients: /api/v1/x == /x.
    if request.scope["path"].startswith("/api/v1/"):
        request.scope["path"] = request.scope["path"][7:]
        request.scope["raw_path"] = request.scope["path"].encode()

    try:
        response = _guard(request) or await call_next(request)
    except BaseException:
        observability.request_id_var.reset(token)
        raise

    elapsed = time.perf_counter() - started
    await _security_event(request, response.status_code)
    route = request.scope.get("route")
    observability.metrics.observe(request.method, getattr(route, "path", "unmatched"), response.status_code,
                                  elapsed)
    user = getattr(request.state, "user", None)
    logger.info(
        "%s %s %s %.0fms user=%s rid=%s",
        request.method, request.url.path, response.status_code, elapsed * 1000,
        user.username if user else "-", request_id,
    )
    observability.request_id_var.reset(token)

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    if request.url.path.startswith("/app"):
        # The camera is used only for barcode scanning, and only by this origin.
        response.headers["Permissions-Policy"] = "camera=(self), microphone=(), geolocation=()"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
    else:
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Cache-Control"] = "no-store"
    if settings.cookie_secure:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


# ============================================================
# ERROR HANDLING
# ============================================================
# Database errors become clear 4xx responses; anything unexpected is logged
# with a request id and returned as a generic 500 (no internal details).

_CONSTRAINT_MESSAGES = {
    "medicines_identity_unique": "A medicine with the same name, strength and dosage form already exists",
    "suppliers_name_unique": "A supplier with this name already exists",
    "purchase_orders_order_number_key": "Purchase order number already exists",
    "batches_medicine_batch_location_unique": "This batch number already exists for the medicine at this location",
    "batches_quantity_non_negative": "Stock cannot go below zero",
    "quantity_received_not_more_than_ordered": "Received quantity exceeds the quantity ordered",
    "users_username_unique": "Username already exists",
    "locations_name_unique": "A location with this name already exists",
}


def _constraint_message(error: psycopg.Error, fallback: str) -> str:
    name = getattr(error.diag, "constraint_name", None)
    return _CONSTRAINT_MESSAGES.get(name, fallback)


@app.exception_handler(psycopg.errors.UniqueViolation)
async def unique_violation(request: Request, error: psycopg.Error):
    return JSONResponse(status_code=409, content={"detail": _constraint_message(error, "Duplicate record")})


@app.exception_handler(psycopg.errors.ForeignKeyViolation)
async def fk_violation(request: Request, error: psycopg.Error):
    return JSONResponse(status_code=400, content={
        "detail": _constraint_message(error, "A referenced record does not exist or is still in use")})


@app.exception_handler(psycopg.errors.CheckViolation)
async def check_violation(request: Request, error: psycopg.Error):
    return JSONResponse(status_code=400, content={"detail": _constraint_message(error, "Invalid value")})


@app.exception_handler(psycopg.errors.DataError)
async def data_error(request: Request, error: psycopg.Error):
    return JSONResponse(status_code=400, content={"detail": "Invalid value (check dates and numbers)"})


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    problems = []
    for item in error.errors():
        field = ".".join(str(part) for part in item["loc"] if part != "body")
        problems.append({"field": field, "message": item["msg"]})
    return JSONResponse(status_code=422, content={"detail": "Validation failed", "errors": problems})


@app.exception_handler(Exception)
async def unexpected_error(request: Request, error: Exception):
    request_id = getattr(request.state, "request_id", "-")
    logger.exception("Unhandled error rid=%s %s %s", request_id, request.method, request.url.path)
    observability.report_error(error, request_id=request_id, method=request.method, path=request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request_id},
    )


# ============================================================
# ROUTES
# ============================================================

@app.get("/", tags=["System"])
def home():
    return {"message": "PharmaStock API is running", "version": VERSION}


@app.get("/health", tags=["System"])
def health():
    try:
        with database.pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok", "database": "connected"}
    except Exception:
        logger.exception("Health check failed")
        return JSONResponse(status_code=503, content={"status": "error", "database": "unavailable"})


@app.get("/ready", tags=["System"])
def ready():
    """Readiness: database reachable and schema up to date (for load balancers
    and orchestrators). /health is the cheaper liveness check."""
    try:
        with database.pool.connection() as conn:
            pending = pending_migrations(conn)
            conn.commit()
    except Exception:
        logger.exception("Readiness check failed")
        return JSONResponse(status_code=503, content={"status": "not ready", "database": "unavailable"})
    if pending:
        return JSONResponse(status_code=503, content={"status": "not ready", "pending_migrations": pending})
    return {"status": "ready", "database": "connected", "migrations": "current", "version": VERSION,
            "error_reporting": ERROR_REPORTING}


@app.get("/metrics", include_in_schema=False)
def metrics(request: Request):
    if settings.metrics_token:
        if request.headers.get("authorization", "") != f"Bearer {settings.metrics_token}":
            return JSONResponse(status_code=401, content={"detail": "Metrics token required"})
    elif settings.is_production:
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    stats = database.pool.get_stats() if database.pool else {}
    extra = {f"pharmastock_db_pool_{key}": value for key, value in stats.items()
             if key in ("pool_size", "pool_available", "requests_waiting", "requests_num", "connections_errors")}
    return Response(observability.metrics.render(extra), media_type="text/plain; version=0.0.4")


for module in (auth, users, organizations, platform, billing, branding, messaging, medicines, barcode, inventory, stock, stock_control, dispensing, transfers, suppliers, purchasing,
               analytics, reports, delivery, admin, assistant):
    app.include_router(module.router)


@app.get("/app", include_in_schema=False)
def app_redirect():
    return RedirectResponse(url="/app/")


app.mount("/app", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
