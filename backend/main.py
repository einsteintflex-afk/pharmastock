# ============================================================
# PHARMASTOCK API
# ============================================================
# Run from the project root:
#     python -m backend.migrate          (apply database migrations)
#     uvicorn backend.main:app --reload
# The frontend is served at /app.

import asyncio
import json
import logging
import time
import uuid
from contextlib import asynccontextmanager

import psycopg
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import database
from .config import settings
from .migrate import pending_migrations
from .routers import admin, analytics, assistant, auth, inventory, medicines, purchasing, reports, stock, suppliers, users
from .services import notifications

VERSION = "2.0.0"

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("pharmastock")


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
        conn.execute(
            """
            INSERT INTO users (username, full_name, role, password_hash, must_change_password)
            VALUES (%s, 'Administrator', 'ADMINISTRATOR', %s, true)
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
    try:
        with database.pool.connection() as conn:
            result = notifications.refresh(conn)
        logger.info("Notifications refreshed: %s", result)
    except Exception:
        logger.exception("Notification refresh failed")


async def _notification_loop() -> None:
    while True:
        await asyncio.to_thread(_refresh_notifications)
        await asyncio.sleep(settings.notification_refresh_minutes * 60)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _check_migrations()
    _bootstrap_admin()
    database.open_pool()
    task = asyncio.create_task(_notification_loop())
    logger.info("PharmaStock API %s started (%s)", VERSION, settings.environment)
    try:
        yield
    finally:
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


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = uuid.uuid4().hex[:12]
    request.state.request_id = request_id
    started = time.perf_counter()

    response = await call_next(request)

    elapsed = (time.perf_counter() - started) * 1000
    user = getattr(request.state, "user", None)
    logger.info(
        "%s %s %s %.0fms user=%s rid=%s",
        request.method, request.url.path, response.status_code, elapsed,
        user.username if user else "-", request_id,
    )

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/app"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
    else:
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


for module in (auth, users, medicines, inventory, stock, suppliers, purchasing, analytics,
               reports, admin, assistant):
    app.include_router(module.router)


@app.get("/app", include_in_schema=False)
def app_redirect():
    return RedirectResponse(url="/app/")


app.mount("/app", StaticFiles(directory=settings.frontend_dir, html=True), name="frontend")
