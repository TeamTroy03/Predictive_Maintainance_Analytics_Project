"""Assistant endpoints with a fake agent: no LLM, no Databricks."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agent.schemas import AssistantAnswer as AgentAnswer
from app.api import agent as api_agent
from app.core.config import Settings
from app.api.errors import ApiError
from app.api.main import create_app

HEADERS = {"X-API-Key": "test-key"}
PLAN = "Approach: look up availability\n1. get_kpi(...)"


class FakeAgent:
    """Same functions as app.agent.agent, backed by a dict."""

    def __init__(self):
        self.runs: dict[str, dict] = {}
        self.started_with: list[str] = []

    def run_exists(self, run_id):
        return run_id in self.runs

    def pending_plan(self, run_id):
        return PLAN if self.runs.get(run_id, {}).get("pending") else None

    def start_run(self, question, run_id=None):
        self.started_with.append(run_id)
        if "weather" in question:  # judge rejects: nothing to approve
            self.runs[run_id] = {"pending": False}
            return run_id, None, {"answer": AgentAnswer(
                answer="", evidence=[], filters={}, source_measure="",
                limitations=["Plan not approved (reject): out of scope."])}
        self.runs[run_id] = {"pending": True}
        return run_id, PLAN, {}

    def finish_run(self, run_id, approved):
        self.runs[run_id]["pending"] = False
        if not approved:
            return {"answer": AgentAnswer(answer="", evidence=[], filters={}, source_measure="",
                                          limitations=["The user declined the plan."])}
        result = {"value": 0.93, "unit": "ratio (0-1)", "row_count": 12}
        return {
            "answer": AgentAnswer(answer="Availability was 0.93.", evidence=["get_kpi: value=0.93"],
                                  filters={"start_month": "2026-04"}, source_measure="get_kpi",
                                  limitations=[]),
            "trace": [{"tool": "get_kpi", "args": {"kpi_name": "availability"}, "ok": True, "result": result}],
        }


@pytest.fixture
def fake(monkeypatch):
    agent = FakeAgent()
    monkeypatch.setattr(api_agent, "_agent", lambda: agent)
    return agent


@pytest.fixture
def client():
    return TestClient(create_app(Settings(api_keys=SecretStr("test-key"))))


def ask(client, question="What was the fleet availability?", **headers):
    return client.post("/assistant/query", json={"question": question}, headers={**HEADERS, **headers})


def test_query_returns_plan_awaiting_approval(client, fake):
    body = ask(client).json()
    assert body["status"] == "awaiting_approval"
    assert body["plan"] == PLAN
    assert body["result"] is None


def test_run_id_is_the_correlation_id(client, fake):
    response = ask(client, **{"X-Correlation-ID": "demo-1"})
    assert response.headers["X-Correlation-ID"] == "demo-1"
    assert response.json()["run_id"] == "demo-1"
    assert fake.started_with == ["demo-1"]


def test_reused_correlation_id_gets_a_fresh_run_id(client, fake):
    ask(client, **{"X-Correlation-ID": "demo-1"})
    second = ask(client, **{"X-Correlation-ID": "demo-1"}).json()
    assert second["run_id"] != "demo-1"
    assert fake.runs.keys() == {"demo-1", second["run_id"]}


def test_approve_returns_mapped_answer(client, fake):
    run_id = ask(client).json()["run_id"]
    response = client.post("/assistant/approve", json={"run_id": run_id, "approved": True}, headers=HEADERS)
    body = response.json()
    assert response.status_code == 200
    assert body["status"] == "answered"
    result = body["result"]
    assert result["answer"] == "Availability was 0.93."
    assert result["grounded"] is True
    assert result["evidence"] == [{"tool": "get_kpi", "arguments": {"kpi_name": "availability"},
                                   "status": "ok", "result": {"value": 0.93, "unit": "ratio (0-1)", "row_count": 12}}]
    assert result["filters"] == {"start_month": "2026-04"}
    assert result["source_measure"] == "get_kpi"


def test_declined_plan_returns_refusal(client, fake):
    run_id = ask(client).json()["run_id"]
    result = client.post("/assistant/approve", json={"run_id": run_id, "approved": False},
                         headers=HEADERS).json()["result"]
    assert result["grounded"] is False
    assert result["evidence"] == []
    assert result["limitations"] == ["The user declined the plan."]


def test_rejected_plan_is_answered_without_approval(client, fake):
    body = ask(client, "What is the weather?").json()
    assert body["status"] == "answered"
    assert body["result"]["limitations"] == ["Plan not approved (reject): out of scope."]


def test_unknown_run_is_404(client, fake):
    response = client.post("/assistant/approve", json={"run_id": "nope", "approved": True}, headers=HEADERS)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "unknown_run"


def test_second_approval_is_409(client, fake):
    run_id = ask(client).json()["run_id"]
    body = {"run_id": run_id, "approved": True}
    client.post("/assistant/approve", json=body, headers=HEADERS)
    response = client.post("/assistant/approve", json=body, headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "run_not_pending"


def test_malformed_run_id_is_422(client, fake):
    response = client.post("/assistant/approve", json={"run_id": "bad id!", "approved": True}, headers=HEADERS)
    assert response.status_code == 422


def test_assistant_requires_auth(client, fake):
    assert client.post("/assistant/query", json={"question": "hi"}).status_code == 401
    assert client.post("/assistant/approve", json={"run_id": "x", "approved": True}).status_code == 401


def test_missing_azure_config_is_503(client, monkeypatch):
    def broken():
        raise ApiError(503, "assistant_unavailable", "The agent is not configured")

    monkeypatch.setattr(api_agent, "_agent", broken)
    response = ask(client)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "assistant_unavailable"


def test_llm_rate_limit_is_429(client, monkeypatch):
    class RateLimitError(Exception):
        pass

    agent = SimpleNamespace(run_exists=lambda run_id: False, start_run=lambda q, r: (_ for _ in ()).throw(RateLimitError()))
    monkeypatch.setattr(api_agent, "_agent", lambda: agent)
    response = ask(client)
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
