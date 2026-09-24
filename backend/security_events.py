# ============================================================
# SECURITY EVENTS (platform security monitoring)
# ============================================================
# Failed sign-ins and MFA codes, refused authorizations, requests for
# records outside the caller's organization (possible cross-tenant probes)
# and rate-limit hits are written to security_events on their OWN
# connection, so they persist even when the request's transaction is
# rolled back. Only platform administrators can read them. No passwords,
# tokens or request bodies are ever stored.

import logging

from . import database

logger = logging.getLogger("pharmastock.security")

EVENT_TYPES = (
    "LOGIN_FAILED", "LOGIN_LOCKED", "MFA_FAILED", "UNAUTHENTICATED", "FORBIDDEN",
    "NOT_FOUND_ID", "RATE_LIMITED", "CSRF_REFUSED", "PLATFORM_DENIED", "RECOVERY_ACCESS",
)


def record(event_type: str, *, organization_id: int | None = None, user_id: int | None = None,
           username: str | None = None, method: str | None = None, path: str | None = None,
           status_code: int | None = None, ip: str | None = None, request_id: str | None = None,
           detail: str | None = None) -> None:
    if database.pool is None:
        return
    try:
        with database.pool.connection() as conn:
            conn.execute(
                """
                INSERT INTO security_events (organization_id, user_id, username, event_type, method, path,
                                             status_code, ip_address, request_id, detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (organization_id, user_id, (username or "")[:50] or None, event_type, method,
                 (path or "")[:200] or None, status_code, ip, request_id, (detail or "")[:500] or None),
            )
            conn.commit()
    except Exception:  # monitoring must never break a request
        logger.exception("Could not record security event %s", event_type)


def classify(method: str, path: str, status: int, authenticated: bool) -> str | None:
    """Which responses are security-relevant."""
    if status == 429:
        return "RATE_LIMITED"
    if status == 401 and authenticated:
        return "UNAUTHENTICATED"
    if status == 403:
        return "PLATFORM_DENIED" if path.startswith("/platform") else "FORBIDDEN"
    if status == 404 and authenticated and any(part.isdigit() for part in path.split("/")):
        # A signed-in user asking for an id that does not exist in their
        # organization: usually a typo, repeatedly a cross-tenant probe.
        return "NOT_FOUND_ID"
    return None
