# E-mail / SMS outbox, notification preferences and scheduled reports.

from datetime import date, datetime, timedelta

import pytest

from backend.services import delivery, scheduler


class FakeEmail:
    def __init__(self, fail: Exception | None = None):
        self.sent = []
        self.fail = fail

    def send(self, recipient, subject, body, attachment):
        if self.fail:
            raise self.fail
        self.sent.append({"to": recipient, "subject": subject, "body": body, "attachment": attachment})


class FakeSms:
    def __init__(self):
        self.sent = []

    def send(self, recipient, text):
        self.sent.append((recipient, text))


@pytest.fixture
def senders(monkeypatch):
    email, sms = FakeEmail(), FakeSms()
    monkeypatch.setattr(delivery, "email_sender", email)
    monkeypatch.setattr(delivery, "sms_sender", sms)
    return email, sms


def _day(offset):
    return (date.today() + timedelta(days=offset)).isoformat()


def test_next_run_calculation():
    after = datetime(2026, 9, 23, 10, 30)  # a Wednesday
    assert scheduler.next_run("DAILY", 7, None, None, after) == datetime(2026, 9, 24, 7)
    assert scheduler.next_run("DAILY", 11, None, None, after) == datetime(2026, 9, 23, 11)
    assert scheduler.next_run("WEEKLY", 7, 0, None, after) == datetime(2026, 9, 28, 7)   # next Monday
    assert scheduler.next_run("WEEKLY", 12, 2, None, after) == datetime(2026, 9, 23, 12)  # later today
    assert scheduler.next_run("MONTHLY", 6, None, 1, after) == datetime(2026, 10, 1, 6)
    assert scheduler.next_run("MONTHLY", 6, None, 5, datetime(2026, 12, 20)) == datetime(2027, 1, 5, 6)


def test_preferences(api, db):
    missing = api.put("/me/notification-preferences", {"notify_email": True}, role="PHARMACIST")
    assert missing.status_code == 400
    saved = api.put("/me/notification-preferences", {"email": "pharmacist@example.com", "phone": "+233 20 000 0000",
                                                     "notify_email": True, "notify_sms": True,
                                                     "notify_min_severity": "CRITICAL"}, role="PHARMACIST")
    assert saved.status_code == 200, saved.text
    body = api.get("/me/notification-preferences", role="PHARMACIST").json()
    assert body["notify_email"] and body["notify_sms"] and body["channels"]["email"]["configured"] is False
    # The manager wants WARNING and above, by e-mail only.
    api.put("/me/notification-preferences", {"email": "manager@example.com", "notify_email": True,
                                             "notify_min_severity": "WARNING"}, role="MANAGER")
    assert db.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'UPDATE_NOTIFICATION_PREFERENCES'"
                      ).fetchone()["n"] == 2


def test_new_critical_notification_is_queued_by_preference(api, db, senders):
    email, sms = senders
    medicine = api.post("/medicines", {"name": "Outbox Med", "reorder_level": 1}).json()
    api.post("/batches", {"medicine_id": medicine["id"], "batch_number": "OB-EXP", "quantity": 4,
                          "expiry_date": _day(-2)})
    api.post("/notifications/refresh")
    queued = db.execute(
        """
        SELECT d.channel, d.recipient, d.subject FROM notification_deliveries d
        JOIN notifications n ON n.id = d.notification_id
        WHERE n.title LIKE '%%Outbox Med batch OB-EXP%%' ORDER BY d.channel, d.recipient
        """
    ).fetchall()
    assert [(q["channel"], q["recipient"]) for q in queued] == [
        ("EMAIL", "manager@example.com"), ("EMAIL", "pharmacist@example.com"), ("SMS", "+233 20 000 0000")]
    # Refreshing again does not queue duplicates.
    api.post("/notifications/refresh")
    again = db.execute("SELECT COUNT(*) AS n FROM notification_deliveries d JOIN notifications n "
                       "ON n.id = d.notification_id WHERE n.title LIKE '%%OB-EXP%%'").fetchone()["n"]
    assert again == 3

    counts = delivery.process_outbox(db)
    assert counts["sent"] >= 3
    assert any("OB-EXP" in m["subject"] and m["to"] == "pharmacist@example.com" for m in email.sent)
    assert any(to == "+233 20 000 0000" and "OB-EXP" in text for to, text in sms.sent)
    statuses = {r["status"] for r in db.execute("SELECT status FROM notification_deliveries").fetchall()}
    assert statuses == {"SENT"}


def test_warning_goes_to_manager_only(api, db, senders):
    # A LOW STOCK (WARNING) notification: manager (WARNING+) yes, pharmacist (CRITICAL only) no.
    medicine = api.post("/medicines", {"name": "Warn Med", "reorder_level": 10}).json()
    api.post("/batches", {"medicine_id": medicine["id"], "batch_number": "WM-1", "quantity": 3,
                          "expiry_date": _day(400)})
    api.post("/notifications/refresh")
    rows = db.execute("SELECT d.recipient FROM notification_deliveries d JOIN notifications n "
                      "ON n.id = d.notification_id WHERE n.title LIKE '%%Warn Med%%'").fetchall()
    assert [r["recipient"] for r in rows] == ["manager@example.com"]
    delivery.process_outbox(db)


def test_failures_retry_then_fail(api, db, monkeypatch):
    failing = FakeEmail(fail=OSError("connection refused"))
    monkeypatch.setattr(delivery, "email_sender", failing)
    queued = api.post("/notification-deliveries/test", {"channel": "EMAIL"}, role="MANAGER")
    assert queued.status_code == 202
    delivery_id = queued.json()["id"]
    delivery.process_outbox(db)
    row = db.execute("SELECT status, attempts, last_error, next_attempt_at > CURRENT_TIMESTAMP AS later "
                     "FROM notification_deliveries WHERE id = %s", (delivery_id,)).fetchone()
    assert row["status"] == "PENDING" and row["attempts"] == 1 and row["later"] and "refused" in row["last_error"]
    for _ in range(delivery.MAX_ATTEMPTS):
        db.execute("UPDATE notification_deliveries SET next_attempt_at = CURRENT_TIMESTAMP WHERE id = %s",
                   (delivery_id,))
        db.commit()
        delivery.process_outbox(db)
    final = db.execute("SELECT status, attempts FROM notification_deliveries WHERE id = %s", (delivery_id,)).fetchone()
    assert final == {"status": "FAILED", "attempts": delivery.MAX_ATTEMPTS}

    # Retry puts it back in the queue; with a working sender it goes out.
    assert api.post(f"/notification-deliveries/{delivery_id}/retry", role="MANAGER").status_code == 200
    working = FakeEmail()
    monkeypatch.setattr(delivery, "email_sender", working)
    delivery.process_outbox(db)
    assert db.execute("SELECT status FROM notification_deliveries WHERE id = %s",
                      (delivery_id,)).fetchone()["status"] == "SENT"


def test_unconfigured_channel_is_skipped(api, db):
    # Real sender, no SMTP configured in tests.
    delivery_id = api.post("/notification-deliveries/test", {"channel": "EMAIL"}, role="MANAGER").json()["id"]
    delivery.process_outbox(db)
    row = db.execute("SELECT status, last_error FROM notification_deliveries WHERE id = %s", (delivery_id,)).fetchone()
    assert row["status"] == "SKIPPED" and "not configured" in row["last_error"]
    listing = api.get("/notification-deliveries", params={"status": "SKIPPED"})
    assert listing.status_code == 200 and listing.json()["deliveries"][0]["id"] == delivery_id
    assert api.get("/notification-deliveries", role="PHARMACIST").status_code == 403


def test_scheduled_report_crud_and_run(api, db, senders):
    email, _ = senders
    body = {"name": "Weekly expiry", "report_key": "expiry", "format": "xlsx", "frequency": "WEEKLY",
            "day_of_week": 0, "hour": 7, "recipients": ["Owner@Example.com", "owner@example.com", "qa@example.com"]}
    assert api.post("/scheduled-reports", body, role="PHARMACIST").status_code == 403
    assert api.post("/scheduled-reports", {**body, "day_of_week": None}).status_code == 400
    assert api.post("/scheduled-reports", {**body, "report_key": "nope"}).status_code == 400
    assert api.post("/scheduled-reports", {**body, "filters": {"evil": 1}}).status_code == 400
    created = api.post("/scheduled-reports", body)
    assert created.status_code == 201, created.text
    schedule = created.json()
    assert schedule["recipients"] == ["owner@example.com", "qa@example.com"]
    assert datetime.fromisoformat(schedule["next_run_at"]).weekday() == 0

    run = api.post(f"/scheduled-reports/{schedule['id']}/run")
    assert run.status_code == 200 and run.json()["status"].startswith("Queued to 2")
    delivery.process_outbox(db)
    mails = [m for m in email.sent if "Weekly expiry" in m["subject"]]
    assert len(mails) == 2
    name, content_type, content = mails[0]["attachment"]
    assert name.endswith(".xlsx") and content[:2] == b"PK" and "spreadsheetml" in content_type

    updated = api.put(f"/scheduled-reports/{schedule['id']}", {**body, "frequency": "DAILY", "day_of_week": None,
                                                               "filters": {"period_days": 7}})
    assert updated.status_code == 200 and updated.json()["frequency"] == "DAILY"
    assert len(api.get("/scheduled-reports").json()) == 1


def test_due_schedules_run_and_advance(api, db, senders):
    email, _ = senders
    schedule = api.post("/scheduled-reports", {"name": "Daily moves", "report_key": "stock-movements", "format": "csv",
                                               "frequency": "DAILY", "hour": 6, "recipients": ["ops@example.com"],
                                               "filters": {"period_days": 1}}).json()
    db.execute("UPDATE scheduled_reports SET next_run_at = LOCALTIMESTAMP - INTERVAL '1 hour' WHERE id = %s",
               (schedule["id"],))
    db.commit()
    assert scheduler.run_due(db) == 1
    row = db.execute("SELECT next_run_at > LOCALTIMESTAMP AS future, last_status FROM scheduled_reports WHERE id = %s",
                     (schedule["id"],)).fetchone()
    assert row["future"] and row["last_status"].startswith("Queued")
    assert scheduler.run_due(db) == 0
    delivery.process_outbox(db)
    assert any(m["to"] == "ops@example.com" and m["attachment"][0].endswith(".csv") for m in email.sent)
    assert api.delete(f"/scheduled-reports/{schedule['id']}").status_code == 204
    assert db.execute("SELECT COUNT(*) AS n FROM audit_log WHERE action = 'RUN_SCHEDULED_REPORT'").fetchone()["n"] >= 2


def test_real_smtp_delivery_with_attachment(monkeypatch):
    """EmailSender against a real (local, in-process) SMTP server."""
    import dataclasses
    from email import message_from_bytes

    controller_module = pytest.importorskip("aiosmtpd.controller")

    received = []

    class Handler:
        async def handle_DATA(self, server, session, envelope):
            received.append(envelope)
            return "250 OK"

    import socket

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    controller = controller_module.Controller(Handler(), hostname="127.0.0.1", port=port)
    controller.start()
    try:
        monkeypatch.setattr(delivery, "settings", dataclasses.replace(
            delivery.settings, smtp_host="127.0.0.1", smtp_port=port, smtp_security="none",
            smtp_from="PharmaStock <alerts@example.com>"))
        delivery.EmailSender().send("owner@example.com", "Test subject", "Body text",
                                    ("report.csv", "text/csv", b"a,b\n1,2\n"))
    finally:
        controller.stop()
    assert len(received) == 1 and received[0].rcpt_tos == ["owner@example.com"]
    message = message_from_bytes(received[0].content)
    assert message["Subject"] == "Test subject"
    parts = [p for p in message.walk() if p.get_filename() == "report.csv"]
    assert parts and parts[0].get_payload(decode=True) == b"a,b\n1,2\n"


def test_sms_webhook(monkeypatch):
    """SmsSender posts JSON with the bearer token to the configured gateway."""
    import dataclasses
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    calls = []

    class Gateway(BaseHTTPRequestHandler):
        def do_POST(self):
            calls.append((self.headers["Authorization"], json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            self.send_response(202)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Gateway)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setattr(delivery, "settings", dataclasses.replace(
            delivery.settings, sms_provider="webhook",
            sms_webhook_url=f"http://127.0.0.1:{server.server_port}/sms", sms_webhook_token="secret-token"))
        delivery.SmsSender().send("+233200000000", "PharmaStock CRITICAL: test")
    finally:
        server.shutdown()
    assert calls == [("Bearer secret-token", {"to": "+233200000000", "message": "PharmaStock CRITICAL: test"})]
