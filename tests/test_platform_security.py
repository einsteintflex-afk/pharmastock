# Two-step verification (TOTP), privileged platform sessions and step-up,
# platform audit, security events, recovery access, new roles, idempotency.

from datetime import date, timedelta

import psycopg
import pytest

from backend import crypto
from tests.conftest import PASSWORD, TEST_URL, org_connection, totp

ORG2_OWNER = {"username": "sec2owner", "password": "Sec-second-pass-3"}


def _login(api, username, password=PASSWORD):
    response = api.client.post("/auth/login", json={"username": username, "password": password})
    api.client.cookies.clear()
    return response


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


def _sql(query, params=()):
    with psycopg.connect(TEST_URL) as conn:
        conn.execute(query, params)
        conn.commit()


@pytest.fixture(scope="module")
def pharmacist_mfa(api):
    """The pharmacist turns on two-step verification."""
    headers = api.headers("PHARMACIST")
    assert api.client.post("/auth/mfa/setup", json={"password": "wrong"}, headers=headers).status_code == 400
    setup = api.client.post("/auth/mfa/setup", json={"password": PASSWORD}, headers=headers)
    assert setup.status_code == 200, setup.text
    body = setup.json()
    assert body["otpauth_uri"].startswith("otpauth://totp/") and "<svg" in body["qr_svg"]
    secret = body["secret"]
    assert api.client.post("/auth/mfa/enable", json={"code": "000000"}, headers=headers).status_code == 400
    enabled = api.client.post("/auth/mfa/enable", json={"code": crypto.totp_now(secret)}, headers=headers)
    assert enabled.status_code == 200, enabled.text
    codes = enabled.json()["recovery_codes"]
    assert len(codes) == 10
    return {"secret": secret, "codes": codes}


def test_totp_matches_rfc6238_vector():
    # RFC 6238 appendix B, SHA-1, T = 59 s: 94287082 -> last 6 digits.
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # base32("12345678901234567890")
    assert crypto.totp_now(secret, at=59) == "287082"


def test_secrets_are_encrypted_at_rest(api, pharmacist_mfa):
    with psycopg.connect(TEST_URL) as conn:
        row = conn.execute("SELECT mfa_secret_encrypted, mfa_recovery_codes FROM users "
                           "WHERE username = 'pharmacist'").fetchone()
    assert pharmacist_mfa["secret"] not in row[0]
    assert crypto.decrypt(row[0]) == pharmacist_mfa["secret"]
    assert not set(pharmacist_mfa["codes"]) & set(row[1])  # only hashes are stored


def test_login_requires_second_factor(api, pharmacist_mfa):
    first = _login(api, "pharmacist")
    assert first.status_code == 200 and first.json()["mfa_required"] is True
    assert "token" not in first.json()
    challenge = first.json()["challenge_token"]
    wrong = api.client.post("/auth/mfa/verify", json={"challenge_token": challenge, "code": "123456"})
    assert wrong.status_code == 401
    _sql("UPDATE users SET mfa_last_counter = NULL WHERE username = 'pharmacist'")
    code = crypto.totp_now(pharmacist_mfa["secret"])
    ok = api.client.post("/auth/mfa/verify", json={"challenge_token": challenge, "code": code})
    api.client.cookies.clear()
    assert ok.status_code == 200 and ok.json()["user"]["mfa_enabled"] is True
    # The challenge is single-use, and the same code cannot be replayed.
    again = api.client.post("/auth/mfa/verify", json={"challenge_token": challenge, "code": code})
    assert again.status_code == 401
    second = _login(api, "pharmacist").json()["challenge_token"]
    replay = api.client.post("/auth/mfa/verify", json={"challenge_token": second, "code": code})
    assert replay.status_code == 401


def test_recovery_code_works_once(api, pharmacist_mfa):
    code = pharmacist_mfa["codes"][0]
    challenge = _login(api, "pharmacist").json()["challenge_token"]
    assert api.client.post("/auth/mfa/verify", json={"challenge_token": challenge, "code": code}).status_code == 200
    api.client.cookies.clear()
    challenge = _login(api, "pharmacist").json()["challenge_token"]
    assert api.client.post("/auth/mfa/verify", json={"challenge_token": challenge, "code": code}).status_code == 401
    api.client.cookies.clear()
    status = api.get("/auth/mfa", role="PHARMACIST").json()
    assert status["enabled"] and status["recovery_codes_remaining"] == 9


def test_challenge_attempts_are_limited(api, pharmacist_mfa):
    _sql("UPDATE users SET failed_login_count = 0 WHERE username = 'pharmacist'")
    challenge = _login(api, "pharmacist").json()["challenge_token"]
    for _ in range(5):
        api.client.post("/auth/mfa/verify", json={"challenge_token": challenge, "code": "000000"})
    _sql("UPDATE users SET mfa_last_counter = NULL, failed_login_count = 0, locked_until = NULL "
         "WHERE username = 'pharmacist'")
    good = crypto.totp_now(pharmacist_mfa["secret"])
    assert api.client.post("/auth/mfa/verify", json={"challenge_token": challenge, "code": good}).status_code == 401
    api.client.cookies.clear()


def test_platform_console_needs_mfa_session(api):
    assert api.get("/platform/organizations", role="MANAGER").status_code == 403
    assert api.get("/platform/organizations").status_code == 200  # MFA-verified admin session
    # A platform administrator without two-step verification is refused.
    _sql("UPDATE users SET is_platform_admin = true WHERE username = 'owner'")
    token = _login(api, "owner").json()["token"]
    refused = api.client.get("/platform/organizations", headers=_bearer(token))
    assert refused.status_code == 403 and "two-step" in refused.json()["detail"]
    _sql("UPDATE users SET is_platform_admin = false WHERE username = 'owner'")


def test_platform_sessions_are_short(api):
    with psycopg.connect(TEST_URL) as conn:
        hours = conn.execute(
            "SELECT EXTRACT(EPOCH FROM s.expires_at - s.created_at) / 3600 FROM sessions s JOIN users u ON u.id = s.user_id "
            "WHERE u.username = 'administrator' AND s.privileged ORDER BY s.id DESC LIMIT 1").fetchone()[0]
    assert 3.9 < float(hours) <= 4.01


def test_high_risk_platform_action_needs_recent_step_up(api):
    _sql("UPDATE sessions SET mfa_verified_at = CURRENT_TIMESTAMP - interval '1 hour' "
         "WHERE user_id = (SELECT id FROM users WHERE username = 'administrator')")
    body = {"name": "Step Up Pharmacy", "admin_username": "stepupowner", "admin_full_name": "Step Up",
            "admin_password": "Step-up-pass-1"}
    refused = api.post("/platform/organizations", body)
    assert refused.status_code == 403 and "step_up_required" in refused.json()["detail"]
    assert api.post("/auth/mfa/step-up", {"code": "000000"}).status_code == 400
    assert api.post("/auth/mfa/step-up", {"code": totp()}).status_code == 200
    created = api.post("/platform/organizations", body)
    assert created.status_code == 201, created.text


@pytest.fixture(scope="module")
def org2(api):
    created = api.post("/platform/organizations", {
        "name": "Security Second", "plan": "BASIC", "status": "ACTIVE",
        "admin_username": ORG2_OWNER["username"], "admin_full_name": "Second Owner",
        "admin_password": ORG2_OWNER["password"]})
    assert created.status_code == 201, created.text
    assert created.json()["administrator"]["username"] == ORG2_OWNER["username"]
    token = _login(api, ORG2_OWNER["username"], ORG2_OWNER["password"]).json()["token"]
    changed = api.client.post("/auth/change-password", headers=_bearer(token), json={
        "current_password": ORG2_OWNER["password"], "new_password": "Sec-second-new-4"})
    assert changed.status_code == 200
    return {"id": created.json()["organization"]["id"], "headers": _bearer(token)}


def test_new_organization_first_user_is_owner(api, org2):
    me = api.client.get("/auth/me", headers=org2["headers"]).json()
    assert me["role"] == "OWNER" and "billing.manage" in me["permissions"]


def test_platform_actions_go_to_platform_audit(api, org2):
    rows = api.get("/platform/audit", params={"organization_id": org2["id"]}).json()["items"]
    assert any(r["action"] == "ORGANIZATION_CREATED" for r in rows)
    # Nothing secret in the details.
    assert all("password" not in str(r["details"]).lower() for r in rows)
    logins = api.get("/platform/audit", params={"action": "PLATFORM_ADMIN_LOGIN"}).json()["items"]
    assert any(r["details"]["mfa"] is True and r["actor_user_id"] is None for r in logins)


def test_platform_audit_is_append_only(api):
    with psycopg.connect(TEST_URL) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("UPDATE platform_audit SET action = 'X'")
    with psycopg.connect(TEST_URL) as conn:
        with pytest.raises(psycopg.Error):
            conn.execute("DELETE FROM platform_audit")


def test_recovery_access(api, org2):
    admins = api.get(f"/platform/organizations/{org2['id']}/administrators").json()
    owner = next(a for a in admins if a["username"] == ORG2_OWNER["username"])
    short = api.post(f"/platform/organizations/{org2['id']}/recovery-access", {"user_id": owner["id"], "reason": "x"})
    assert short.status_code == 422  # a reason is mandatory
    # Only administrators of THAT organization can be chosen.
    pharmacist = api.get("/users").json()
    other = next(u for u in pharmacist if u["username"] == "pharmacist")
    assert api.post(f"/platform/organizations/{org2['id']}/recovery-access",
                    {"user_id": other["id"], "reason": "Owner lost phone and password"}).status_code == 404

    issued = api.post(f"/platform/organizations/{org2['id']}/recovery-access",
                      {"user_id": owner["id"], "reason": "Owner lost phone and password, verified by call"})
    assert issued.status_code == 201, issued.text
    token = issued.json()["link"].split("token=")[1]
    # Existing sessions stay valid until the password changes; the reset ends them.
    reset = api.client.post("/auth/reset-password", json={"token": token, "new_password": "Recovered-pass-55"})
    assert reset.status_code == 200
    assert api.client.post("/auth/reset-password", json={"token": token, "new_password": "Recovered-pass-56"}).status_code == 400
    assert api.client.get("/auth/me", headers=org2["headers"]).status_code == 401
    login = _login(api, ORG2_OWNER["username"], "Recovered-pass-55")
    assert login.status_code == 200 and "token" in login.json()
    org2["headers"] = _bearer(login.json()["token"])

    # The company sees it in its own audit trail; the platform in the platform audit.
    trail = api.client.get("/audit-log", headers=org2["headers"], params={"action": "PLATFORM_RECOVERY_ACCESS"})
    assert trail.status_code == 200
    entries = trail.json()["items"] if isinstance(trail.json(), dict) else trail.json()
    assert any(e["action"] == "PLATFORM_RECOVERY_ACCESS" for e in entries)
    actions = {r["action"] for r in api.get("/platform/audit", params={"organization_id": org2["id"]}).json()["items"]}
    assert {"RECOVERY_ACCESS_ISSUED", "RECOVERY_ACCESS_USED"} <= actions


def test_recovery_link_can_be_revoked(api, org2):
    owner = next(a for a in api.get(f"/platform/organizations/{org2['id']}/administrators").json()
                 if a["username"] == ORG2_OWNER["username"])
    issued = api.post(f"/platform/organizations/{org2['id']}/recovery-access",
                      {"user_id": owner["id"], "reason": "Second request, to be revoked"}).json()
    token = issued["link"].split("token=")[1]
    active = next(r for r in api.get("/platform/recovery-access").json() if r["used_at"] is None and not r["revoked_at"])
    assert api.delete(f"/platform/recovery-access/{active['id']}").status_code == 200
    assert api.client.post("/auth/reset-password", json={"token": token, "new_password": "Recovered-pass-77"}).status_code == 400


def test_security_events_record_probes_and_failures(api, org2):
    _login(api, "viewer", "wrong-password")
    # Organization 2 asks for organization 1's medicine: not found (RLS), recorded.
    medicine_id = api.get("/medicines").json()[0]["id"]
    assert api.client.get(f"/medicines/{medicine_id}", headers=org2["headers"]).status_code == 404
    assert api.get("/users", role="VIEWER").status_code == 403
    events = api.get("/platform/security-events").json()
    kinds = {e["event_type"] for e in events["items"]}
    assert {"LOGIN_FAILED", "NOT_FOUND_ID", "FORBIDDEN"} <= kinds
    probe = next(e for e in events["items"] if e["event_type"] == "NOT_FOUND_ID")
    assert probe["organization_id"] == org2["id"] and probe["path"] == f"/medicines/{medicine_id}"
    assert api.get("/platform/security-events", role="MANAGER").status_code == 403


def test_owner_role_is_protected(api):
    body = {"username": "newowner", "full_name": "New Owner", "role": "OWNER", "password": "Owner-pass-2026"}
    assert api.post("/users", body).status_code == 403  # an administrator cannot create an owner
    assert api.post("/users", body, role="OWNER").status_code == 201
    owner = next(u for u in api.get("/users").json() if u["username"] == "newowner")
    assert api.put(f"/users/{owner['id']}", {"full_name": "New Owner", "role": "VIEWER",
                                             "is_active": True}).status_code == 403
    assert api.post(f"/users/{owner['id']}/reset-password", {}).status_code == 403


@pytest.mark.parametrize("role,allowed,denied", [
    ("CASHIER", ["/dispensations"], ["/reports/valuation", "/audit-log", "/assistant/status"]),
    ("AUDITOR", ["/audit-log", "/reports/valuation"], []),
    ("INVENTORY_OFFICER", ["/medicines"], ["/audit-log", "/users"]),
    ("PURCHASING_OFFICER", ["/purchase-orders"], ["/users", "/audit-log"]),
])
def test_new_role_permissions(api, role, allowed, denied):
    for path in allowed:
        assert api.get(path, role=role).status_code == 200, (role, path)
    for path in denied:
        assert api.get(path, role=role).status_code == 403, (role, path)
    perms = set(api.get("/auth/me", role=role).json()["permissions"])
    if role == "AUDITOR":
        assert not perms & {"stock.dispense", "stock.adjust", "medicines.write", "users.manage"}
    if role == "CASHIER":
        assert perms == {"inventory.read", "notifications.read", "stock.dispense"}


def test_organization_can_require_mfa_for_administrators(api):
    assert api.put("/settings", {"security.require_mfa_for_admins": True}).status_code == 200
    blocked = api.get("/medicines", role="OWNER")  # the owner has no MFA yet
    assert blocked.status_code == 403 and "two-step" in blocked.json()["detail"]
    assert api.get("/auth/mfa", role="OWNER").json()["required"] is True
    assert api.get("/medicines", role="PHARMACIST").status_code == 200  # other roles unaffected
    assert api.put("/settings", {"security.require_mfa_for_admins": False}).status_code == 200
    assert api.get("/medicines", role="OWNER").status_code == 200


def test_admin_can_reset_a_users_mfa(api, pharmacist_mfa):
    pharmacist = next(u for u in api.get("/users").json() if u["username"] == "pharmacist")
    assert pharmacist["mfa_enabled"] is True
    assert api.post(f"/users/{pharmacist['id']}/reset-mfa").status_code == 200
    api.tokens.pop("PHARMACIST", None)
    login = _login(api, "pharmacist")
    assert "token" in login.json()  # password only again
    trail = api.get("/audit-log", params={"action": "MFA_RESET"}).json()
    assert trail


def test_dispensation_idempotency_key(api, db):
    medicine = api.post("/medicines", {"name": "Idem Tabs", "strength": "5 mg", "dosage_form": "Tablet",
                                       "reorder_level": 1, "selling_price": 2}).json()
    batch = api.post("/batches", {"medicine_id": medicine["id"], "batch_number": "IDEM-1", "quantity": 20,
                                  "expiry_date": (date.today() + timedelta(days=400)).isoformat()}).json()
    sale = {"dispense_type": "OTC", "payment_method": "CASH",
            "items": [{"medicine_id": medicine["id"], "quantity": 3}]}
    headers = {**api.headers("ADMINISTRATOR"), "Idempotency-Key": "sale-abc-1"}
    first = api.client.post("/dispensations", json=sale, headers=headers)
    second = api.client.post("/dispensations", json=sale, headers=headers)
    assert first.status_code == 201 and second.status_code == 201
    assert second.headers.get("Idempotent-Replay") == "true"
    assert first.json()["id"] == second.json()["id"]
    quantity = db.execute("SELECT quantity FROM batches WHERE id = %s", (batch["id"],)).fetchone()["quantity"]
    assert quantity == 17  # sold once
    changed = api.client.post("/dispensations", json={**sale, "items": [{"medicine_id": medicine["id"], "quantity": 1}]},
                              headers=headers)
    assert changed.status_code == 422


def test_idempotency_keys_are_per_organization(api):
    with org_connection(1) as conn:
        count = conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0]
    with org_connection(999) as conn:
        assert conn.execute("SELECT COUNT(*) FROM idempotency_keys").fetchone()[0] == 0
    assert count >= 1


def test_application_role_has_least_privilege(api):
    import os

    from tests.conftest import APP_ROLE
    if APP_ROLE == "owner":
        pytest.skip("API runs as the owner (TEST_APP_ROLE=owner)")
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        role = conn.execute("SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb FROM pg_roles "
                            "WHERE rolname = current_user").fetchone()
        assert role == (False, False, False, False)
        owner = conn.execute("SELECT tableowner = current_user FROM pg_tables WHERE tablename = 'batches'").fetchone()[0]
        assert owner is False
    for statement in ("UPDATE audit_log SET action = 'X'", "DELETE FROM audit_log",
                      "DELETE FROM security_events", "INSERT INTO schema_migrations VALUES ('9999', 'x', 'x')",
                      "DROP TABLE batches", "ALTER TABLE batches DISABLE ROW LEVEL SECURITY",
                      "CREATE TABLE intruder (id int)", "TRUNCATE stock_movements"):
        with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)
    # Without an organization context the application role sees no tenant rows.
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM batches").fetchone()[0] == 0
