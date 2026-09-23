# Authentication: login, logout, sessions, lockout, password management.

from tests.conftest import PASSWORD


def test_protected_endpoints_require_login(api):
    for path in ("/medicines", "/inventory", "/stock-alerts", "/stock-movements", "/suppliers",
                 "/purchase-orders", "/dashboard", "/reports", "/audit-log", "/users"):
        response = api.client.get(path)
        assert response.status_code == 401, path


def test_public_endpoints(api):
    assert api.client.get("/").status_code == 200
    assert api.client.get("/health").json()["database"] == "connected"
    assert api.client.get("/app/").status_code == 200


def test_login_success_returns_token_and_cookie(api):
    response = api.client.post("/auth/login", json={"username": "PHARMACIST", "password": PASSWORD})
    assert response.status_code == 200
    body = response.json()
    assert body["user"]["role"] == "PHARMACIST"
    assert "stock.dispense" in body["user"]["permissions"]
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie
    # Cookie session works for the browser.
    assert api.client.get("/auth/me").json()["username"] == "pharmacist"
    api.client.cookies.clear()


def test_login_failure_is_generic(api):
    wrong = api.client.post("/auth/login", json={"username": "viewer", "password": "nope"})
    unknown = api.client.post("/auth/login", json={"username": "ghost", "password": "nope"})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Invalid username or password"}


def test_password_stored_hashed(db):
    row = db.execute("SELECT password_hash FROM users WHERE username = 'viewer'").fetchone()
    assert row["password_hash"].startswith("scrypt$") and PASSWORD not in row["password_hash"]
    tokens = db.execute("SELECT token_hash FROM sessions").fetchall()
    assert all(len(t["token_hash"]) == 64 for t in tokens)


def test_logout_revokes_session(api):
    token = api.client.post("/auth/login", json={"username": "storekeeper", "password": PASSWORD}).json()["token"]
    api.client.cookies.clear()
    headers = {"Authorization": f"Bearer {token}"}
    assert api.client.get("/auth/me", headers=headers).status_code == 200
    assert api.client.post("/auth/logout", headers=headers).status_code == 200
    assert api.client.get("/auth/me", headers=headers).status_code == 401
    api.client.cookies.clear()


def test_lockout_after_repeated_failures(api, db):
    api.post("/users", {"username": "locktest", "full_name": "Lock Test", "role": "VIEWER",
                        "password": "Initial-pass-99"})
    for _ in range(5):
        assert api.client.post("/auth/login", json={"username": "locktest", "password": "bad"}).status_code == 401
    # Even the correct password is refused while locked.
    response = api.client.post("/auth/login", json={"username": "locktest", "password": "Initial-pass-99"})
    assert response.status_code == 423
    row = db.execute("SELECT locked_until FROM users WHERE username = 'locktest'").fetchone()
    assert row["locked_until"] is not None
    actions = [r["action"] for r in db.execute(
        "SELECT action FROM audit_log WHERE username = 'locktest' ORDER BY id")]
    assert actions.count("LOGIN_FAILED") == 5 and "LOGIN_BLOCKED" in actions


def test_new_user_must_change_password(api):
    created = api.post("/users", {"username": "newtech", "full_name": "New Tech", "role": "PHARMACY_TECHNICIAN",
                                  "password": "Initial-pass-11"})
    assert created.status_code == 201
    token = api.client.post("/auth/login", json={"username": "newtech", "password": "Initial-pass-11"}).json()["token"]
    api.client.cookies.clear()
    headers = {"Authorization": f"Bearer {token}"}
    assert api.client.get("/medicines", headers=headers).status_code == 403

    weak = api.client.post("/auth/change-password", headers=headers,
                           json={"current_password": "Initial-pass-11", "new_password": "short"})
    assert weak.status_code == 400
    wrong = api.client.post("/auth/change-password", headers=headers,
                            json={"current_password": "wrong", "new_password": "Better-pass-2026"})
    assert wrong.status_code == 400
    ok = api.client.post("/auth/change-password", headers=headers,
                         json={"current_password": "Initial-pass-11", "new_password": "Better-pass-2026"})
    assert ok.status_code == 200
    assert api.client.get("/medicines", headers=headers).status_code == 200


def test_admin_reset_password_revokes_sessions(api):
    users = api.get("/users").json()
    target = next(u for u in users if u["username"] == "newtech")
    old_token = api.client.post("/auth/login", json={"username": "newtech", "password": "Better-pass-2026"}).json()["token"]
    api.client.cookies.clear()
    reset = api.post(f"/users/{target['id']}/reset-password", {})
    assert reset.status_code == 200
    temporary = reset.json()["temporary_password"]
    assert api.client.get("/auth/me", headers={"Authorization": f"Bearer {old_token}"}).status_code == 401
    login = api.client.post("/auth/login", json={"username": "newtech", "password": temporary})
    api.client.cookies.clear()
    assert login.json()["user"]["must_change_password"] is True


def test_deactivated_user_cannot_sign_in(api):
    users = api.get("/users").json()
    target = next(u for u in users if u["username"] == "locktest")
    api.put(f"/users/{target['id']}", {"full_name": "Lock Test", "role": "VIEWER", "is_active": False})
    response = api.client.post("/auth/login", json={"username": "locktest", "password": "Initial-pass-99"})
    assert response.status_code in (401, 423)


def test_last_admin_protected(api):
    admin = next(u for u in api.get("/users").json() if u["username"] == "administrator")
    response = api.put(f"/users/{admin['id']}", {"full_name": "x", "role": "VIEWER", "is_active": True})
    assert response.status_code == 400
