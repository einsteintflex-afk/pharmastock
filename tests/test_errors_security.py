# Error handling, input validation and security headers.

from fastapi import APIRouter

from backend.main import app


def test_validation_errors_are_structured(api):
    response = api.post("/medicines", {"name": "X", "reorder_level": "lots"})
    assert response.status_code == 422
    body = response.json()
    assert body["detail"] == "Validation failed" and body["errors"][0]["field"] == "reorder_level"


def test_malformed_json(api):
    response = api.client.post("/medicines", content=b"{not json", headers={
        **api.headers("ADMINISTRATOR"), "Content-Type": "application/json"})
    assert response.status_code == 422


def test_sql_injection_is_inert(api, db):
    before = db.execute("SELECT COUNT(*) AS n FROM medicines").fetchone()["n"]
    response = api.get("/medicines", params={"search": "'; DROP TABLE medicines; --"})
    assert response.status_code == 200 and response.json() == []
    assert db.execute("SELECT COUNT(*) AS n FROM medicines").fetchone()["n"] == before


def test_oversized_input_rejected(api):
    assert api.post("/medicines", {"name": "x" * 500}).status_code == 422


def test_unexpected_errors_hide_details(api):
    router = APIRouter()

    @router.get("/__boom")
    def boom():
        raise RuntimeError("secret internal detail")

    app.include_router(router)
    api.client._transport.raise_server_exceptions = False
    try:
        response = api.client.get("/__boom")
        assert response.status_code == 500
        assert "secret" not in response.text and "request_id" in response.json()
    finally:
        api.client._transport.raise_server_exceptions = True
        app.router.routes = [r for r in app.router.routes if getattr(r, "path", "") != "/__boom"]


def test_security_headers(api):
    page = api.client.get("/app/")
    assert "script-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["x-frame-options"] == "DENY"
    assert page.headers["x-content-type-options"] == "nosniff"
    data = api.get("/medicines")
    assert data.headers["cache-control"] == "no-store"
    assert data.headers["x-request-id"]


def test_invalid_token_rejected(api):
    assert api.client.get("/medicines", headers={"Authorization": "Bearer forged"}).status_code == 401


def test_backup_files_not_served(api):
    for path in ("/app/app_backup.js", "/app/index_backup.html", "/app/../.env", "/app/../backend/config.py"):
        assert api.client.get(path).status_code == 404, path
