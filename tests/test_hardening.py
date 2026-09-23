# Rate limiting, CSRF / origin checks, API versioning, readiness, metrics,
# structured logs, error reporting, sessions and password reset.

import json
import logging
import re

import pytest

from backend import main, observability
from tests.conftest import PASSWORD


@pytest.fixture
def limits():
    """Temporarily enable small rate limits."""
    saved = main.auth_limiter.limit, main.api_limiter.limit
    main.auth_limiter.reset()
    main.api_limiter.reset()
    yield
    main.auth_limiter.limit, main.api_limiter.limit = saved
    main.auth_limiter.reset()
    main.api_limiter.reset()


def test_login_rate_limit(api, limits):
    main.auth_limiter.limit = 3
    for _ in range(3):
        assert api.client.post("/auth/login", json={"username": "nobody", "password": "x"}).status_code == 401
    blocked = api.client.post("/auth/login", json={"username": "nobody", "password": "x"})
    assert blocked.status_code == 429 and int(blocked.headers["retry-after"]) >= 1


def test_api_rate_limit_per_session(api, limits):
    main.api_limiter.limit = 5
    statuses = [api.get("/medicines", role="VIEWER").status_code for _ in range(7)]
    assert statuses[:5] == [200] * 5 and statuses[5:] == [429, 429]
    # Another session has its own allowance; health checks are never limited.
    assert api.get("/medicines", role="MANAGER").status_code == 200
    assert api.client.get("/health").status_code == 200


def test_csrf_header_required_for_cookie_sessions(api):
    login = api.client.post("/auth/login", json={"username": "viewer", "password": PASSWORD})
    assert login.status_code == 200
    try:
        # Cookie-authenticated state change without the application header.
        bare = api.client.post("/notifications/read-all")
        assert bare.status_code == 403 and "CSRF" in bare.json()["detail"]
        ok = api.client.post("/notifications/read-all", headers={"X-Requested-With": "PharmaStock"})
        assert ok.status_code == 200
        # Reads are unaffected.
        assert api.client.get("/medicines").status_code == 200
    finally:
        api.client.cookies.clear()


def test_foreign_origin_refused(api):
    auth = api.headers("ADMINISTRATOR")
    evil = api.client.post("/suppliers", json={"name": "Origin Test"},
                           headers={**auth, "Origin": "https://evil.example"})
    assert evil.status_code == 403
    same = api.client.post("/suppliers", json={"name": "Origin Test"}, headers={**auth, "Origin": "http://testserver"})
    assert same.status_code == 200


def test_versioned_api_path(api):
    plain = api.get("/medicines").json()
    versioned = api.get("/api/v1/medicines")
    assert versioned.status_code == 200 and versioned.json() == plain
    assert api.client.post("/api/v1/auth/login", json={"username": "viewer", "password": PASSWORD}).status_code == 200
    api.client.cookies.clear()


def test_ready_and_metrics(api):
    ready = api.client.get("/ready")
    assert ready.status_code == 200 and ready.json()["migrations"] == "current"
    api.get("/medicines")
    text = api.client.get("/metrics").text
    assert 'pharmastock_requests_total{method="GET",route="/medicines",status="200"}' in text
    assert "pharmastock_request_seconds_bucket" in text and "pharmastock_db_pool_pool_size" in text


def test_json_log_format_includes_request_id():
    formatter = observability.JsonFormatter()
    record = logging.LogRecord("pharmastock", logging.INFO, __file__, 1, "hello %s", ("world",), None)
    record.request_id = "abc123"
    payload = json.loads(formatter.format(record))
    assert payload["message"] == "hello world" and payload["request_id"] == "abc123" and payload["level"] == "INFO"


def test_error_webhook(monkeypatch):
    sent = []
    monkeypatch.setattr(observability, "_webhook_url", "http://hook.invalid/errors")
    monkeypatch.setattr(observability, "_sentry", None)
    monkeypatch.setattr(observability, "_last_sent", {})

    class Immediate:
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            sent.append(json.loads(self.args[0].data))

    monkeypatch.setattr(observability.threading, "Thread", Immediate)
    error = RuntimeError("boom")
    observability.report_error(error, request_id="r1", method="GET", path="/x")
    observability.report_error(error, request_id="r2", method="GET", path="/x")  # de-duplicated
    assert len(sent) == 1 and sent[0]["request_id"] == "r1" and "RuntimeError" in sent[0]["error"]


def test_sessions_list_and_revoke(api, db):
    first = api.client.post("/auth/login", json={"username": "storekeeper", "password": PASSWORD,
                                                 "client_name": "Store tablet"}).json()["token"]
    api.client.cookies.clear()
    second = api.client.post("/auth/login", json={"username": "storekeeper", "password": PASSWORD,
                                                  "client_name": "Android phone"}).json()["token"]
    api.client.cookies.clear()
    headers = {"Authorization": f"Bearer {second}"}
    sessions = api.client.get("/auth/sessions", headers=headers).json()
    names = {s["client_name"]: s for s in sessions}
    assert names["Android phone"]["current"] and not names["Store tablet"]["current"]

    assert api.client.delete(f"/auth/sessions/{names['Store tablet']['id']}", headers=headers).status_code == 200
    assert api.client.get("/auth/me", headers={"Authorization": f"Bearer {first}"}).status_code == 401
    # Another user's session id is not found.
    other = db.execute("SELECT id FROM sessions WHERE user_id <> (SELECT id FROM users WHERE username = "
                       "'storekeeper') LIMIT 1").fetchone()["id"]
    assert api.client.delete(f"/auth/sessions/{other}", headers=headers).status_code == 404

    third = api.client.post("/auth/login", json={"username": "storekeeper", "password": PASSWORD}).json()["token"]
    api.client.cookies.clear()
    assert api.client.post("/auth/logout-others", headers=headers).status_code == 200
    assert api.client.get("/auth/me", headers={"Authorization": f"Bearer {third}"}).status_code == 401
    assert api.client.get("/auth/me", headers=headers).status_code == 200

    # Administrators can see and revoke a user's sessions.
    user_id = db.execute("SELECT id FROM users WHERE username = 'storekeeper'").fetchone()["id"]
    assert len(api.get(f"/users/{user_id}/sessions").json()) == 1
    assert api.post(f"/users/{user_id}/revoke-sessions").status_code == 200
    assert api.client.get("/auth/me", headers=headers).status_code == 401


def _token_from(body: str) -> str:
    return re.search(r"token=([A-Za-z0-9_\-]+)", body).group(1)


def test_forgot_and_reset_password(api, db):
    db.execute("UPDATE users SET email = 'tech@example.com' WHERE username = 'pharmacy_technician'")
    db.commit()
    generic = api.client.post("/auth/forgot-password", json={"username": "pharmacy_technician"})
    unknown = api.client.post("/auth/forgot-password", json={"username": "no-such-user"})
    assert generic.status_code == unknown.status_code == 202 and generic.json() == unknown.json()

    mail = db.execute("SELECT body, recipient FROM notification_deliveries WHERE subject = 'PharmaStock password reset' "
                      "ORDER BY id DESC LIMIT 1").fetchone()
    assert mail["recipient"] == "tech@example.com"
    token = _token_from(mail["body"])
    assert db.execute("SELECT COUNT(*) AS n FROM password_reset_tokens WHERE token_hash = %s",
                      (token,)).fetchone()["n"] == 0  # only the hash is stored

    weak = api.client.post("/auth/reset-password", json={"token": token, "new_password": "short"})
    assert weak.status_code == 400
    done = api.client.post("/auth/reset-password", json={"token": token, "new_password": "Brand-new-pass-44"})
    assert done.status_code == 200
    assert api.client.post("/auth/reset-password", json={"token": token, "new_password": "Another-pass-55"}
                           ).status_code == 400  # one-time
    login = api.client.post("/auth/login", json={"username": "pharmacy_technician", "password": "Brand-new-pass-44"})
    assert login.status_code == 200
    api.client.cookies.clear()
    api.tokens.pop("PHARMACY_TECHNICIAN", None)
    actions = [r["action"] for r in db.execute("SELECT action FROM audit_log WHERE action LIKE 'PASSWORD_RESET_%%' "
                                                "ORDER BY id").fetchall()]
    assert actions[-2:] == ["PASSWORD_RESET_REQUESTED", "PASSWORD_RESET_COMPLETED"]
    # Restore the shared test password.
    from backend.security import hash_password
    db.execute("UPDATE users SET password_hash = %s WHERE username = 'pharmacy_technician'", (hash_password(PASSWORD),))
    db.commit()


def test_admin_reset_link(api, db):
    user_id = db.execute("SELECT id FROM users WHERE username = 'viewer'").fetchone()["id"]
    assert api.post(f"/users/{user_id}/reset-link", {"send_email": True}, role="MANAGER").status_code == 403
    link = api.post(f"/users/{user_id}/reset-link", {"send_email": True}).json()
    assert link["emailed"] is False and "/app/#/reset-password?token=" in link["link"]
    token = _token_from(link["link"])
    # A newer link voids the older one.
    newer = _token_from(api.post(f"/users/{user_id}/reset-link", {"send_email": False}).json()["link"])
    assert api.client.post("/auth/reset-password", json={"token": token, "new_password": "Viewer-new-pass-1"}
                           ).status_code == 400
    assert api.client.post("/auth/reset-password", json={"token": newer, "new_password": PASSWORD + "x"}
                           ).status_code == 200
    from backend.security import hash_password
    db.execute("UPDATE users SET password_hash = %s WHERE username = 'viewer'", (hash_password(PASSWORD),))
    db.commit()
    api.tokens.pop("VIEWER", None)
