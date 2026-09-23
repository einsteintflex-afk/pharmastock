# AI assistant: answers come from real data (built-in engine), and the
# Claude tool-use loop executes real tools (Claude client mocked; no network).

from types import SimpleNamespace

import pytest

from backend.config import settings
from backend.services import assistant


def ask(api, question, role="VIEWER"):
    response = api.post("/assistant/ask", {"question": question}, role=role)
    assert response.status_code == 200, response.text
    return response.json()


def test_status(api):
    assert api.get("/assistant/status").json()["engine"] == "built-in"


def test_fefo_question_uses_batch_data(api, db):
    answer = ask(api, "Which batches of Paracetamol should be used first?")
    assert answer["tools_used"] == ["fefo_order"]
    usable = [r["batch_number"] for r in db.execute(
        "SELECT batch_number FROM batches WHERE medicine_id = 1 AND quantity > 0 AND expiry_date >= CURRENT_DATE "
        "ORDER BY expiry_date")]
    text = answer["answer"]
    positions = [text.index(b) for b in usable]
    assert positions == sorted(positions)  # listed in expiry order
    assert "PARA004" not in text  # expired batch excluded


def test_fefo_allocation_for_quantity(api):
    text = ask(api, "Which batch should I dispense first for 60 Paracetamol?")["answer"]
    assert "For 60 units" in text


@pytest.mark.parametrize("question,tool", [
    ("Which medicines are at highest expiry risk?", "expiry_risk"),
    ("What needs reordering?", "reorder_recommendations"),
    ("Which medicines are slow-moving?", "slow_moving"),
    ("What stock is currently low?", "low_stock"),
    ("What is the value of stock approaching expiry?", "valuation"),
    ("Show expired batches", "expiry_alerts"),
    ("How much Amoxicillin do we have?", "medicine_stock"),
    ("hello", "inventory_overview"),
    ("Give me a summary of this month", "monthly_summary"),
    ("How has stock changed this month?", "stock_changes"),
    ("Who are our top suppliers?", "top_suppliers"),
    ("Which medicines have high stock but low consumption?", "overstock"),
    ("What is the value of expired stock?", "expired_stock"),
    ("Have we had any stock-outs?", "stockouts"),
])
def test_sample_questions(api, question, tool):
    answer = ask(api, question)
    assert answer["tools_used"] == [tool]
    assert answer["answer"]


def test_low_stock_answer_matches_data(api):
    assert "Ibuprofen" in ask(api, "What stock is currently low?")["answer"]


def test_assistant_requires_login_and_is_audited(api, db):
    assert api.client.post("/assistant/ask", json={"question": "What needs reordering?"}).status_code == 401
    row = db.execute("SELECT new_value FROM audit_log WHERE action = 'ASSISTANT_QUERY' ORDER BY id DESC LIMIT 1").fetchone()
    assert row["new_value"]["engine"] == "built-in"


def test_claude_tool_loop_executes_real_tools(api, db, monkeypatch):
    calls = []

    class FakeMessages:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return SimpleNamespace(stop_reason="tool_use", model="m", content=[
                    SimpleNamespace(type="tool_use", id="t1", name="low_stock", input={})])
            tool_result = kwargs["messages"][-1]["content"][0]
            assert tool_result["tool_use_id"] == "t1" and "Ibuprofen" in tool_result["content"]
            return SimpleNamespace(stop_reason="end_turn", model="m",
                                   content=[SimpleNamespace(type="text", text="Ibuprofen is out of stock.")])

    class FakeClient:
        def __init__(self, **kwargs):
            self.beta = SimpleNamespace(messages=FakeMessages())

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    object.__setattr__(settings, "anthropic_api_key", "test-key")
    try:
        with psycopg_conn() as conn:
            result = assistant.ask(conn, "What is low?")
    finally:
        object.__setattr__(settings, "anthropic_api_key", None)

    assert result["engine"] == "claude" and result["tools_used"] == ["low_stock"]
    assert result["answer"] == "Ibuprofen is out of stock."
    assert calls[0]["model"] == settings.anthropic_model
    assert {t["name"] for t in calls[0]["tools"]} == set(assistant.TOOLS)


def test_claude_failure_falls_back(monkeypatch):
    class Broken:
        def __init__(self, **kwargs):
            raise RuntimeError("network down")

    import anthropic
    monkeypatch.setattr(anthropic, "Anthropic", Broken)
    object.__setattr__(settings, "anthropic_api_key", "test-key")
    try:
        with psycopg_conn() as conn:
            result = assistant.ask(conn, "What needs reordering?")
    finally:
        object.__setattr__(settings, "anthropic_api_key", None)
    assert result["engine"] == "built-in" and "notice" in result


def psycopg_conn():
    from psycopg.rows import dict_row
    from tests.conftest import org_connection
    return org_connection(1, row_factory=dict_row)


def test_unknown_tool_and_bad_args_are_errors(db):
    assert "error" in assistant.run_tool(db, "drop_tables", {})
    assert "error" in assistant.tool_fefo(db, "no-such-medicine")
