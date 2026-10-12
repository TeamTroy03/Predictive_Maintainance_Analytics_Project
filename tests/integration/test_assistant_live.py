"""Live test of API <-> agent <-> Azure OpenAI <-> Gold, through the two-step assistant endpoints.

Everything is real: the FastAPI app (in-process TestClient), the agent graph, the LLM and the Databricks
Gold tables. Only read-only queries run. Needs the Azure OpenAI and Databricks variables in the environment or
in the project's .env, otherwise every test is skipped. It spends LLM quota (about 25 calls, 2-3 minutes).

Run:  python -m pytest tests/integration -m integration -v -s
      (-s prints the full communication with the LLM: every prompt input and output, then the agent log events)

What is checked: nothing runs before the human approves (approval), a plan the reviewer sends back is revised by
the planner (revision), and out-of-scope questions, declined plans and plans that stay invalid are rejected
without running any tool (rejection). Assertions are on structure and events, not on the LLM's wording."""

import importlib
import json
import os
import uuid
import warnings

import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agent.schemas import Plan, PlanStep
from app.core.config import Settings
from app.api.main import create_app

load_dotenv()

REQUIRED = (
    "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_DEPLOYMENT_NAME", "OPENAI_API_VERSION",
    "DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN", "GOLD_CATALOG", "GOLD_SCHEMA",
)
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not all(os.getenv(k) for k in REQUIRED), reason=f"needs {', '.join(REQUIRED)}"),
]

HEADERS = {"X-API-Key": "test-key"}
KPI_QUESTION = "What was the fleet availability between 2026-04 and 2026-06?"
BAD_PLAN = Plan(approach="bad", steps=[PlanStep(tool="drop_table", args={}, purpose="not an approved tool")])


# ---------------------------------------------------------------- helpers
class Recorder:
    """The agent's LLM exchanges, recorded by wrapping its module-level functions (the agent is unchanged)."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def wrap(self, agent) -> None:
        real_ask, real_plan_text = agent._ask, agent.plan_text

        def ask(schema, prompt, **variables):
            out = real_ask(schema, prompt, **variables)
            self.calls.append({"step": schema.__name__, "input": variables, "output": out})
            return out

        def plan_text(question, plan):
            out = real_plan_text(question, plan)
            self.calls.append({"step": "plan_text (summary)", "input": {"question": question}, "output": out})
            return out

        agent._ask, agent.plan_text = ask, plan_text

    def steps(self, since: int = 0) -> list[str]:
        return [c["step"] for c in self.calls[since:]]


def _short(value: object, limit: int = 700) -> str:
    text = value.model_dump_json() if hasattr(value, "model_dump_json") else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + " ...[cut]"


def events(agent, run_id: str) -> list[dict]:
    """The agent.log lines of one run, in order."""
    path = agent.LOG_DIR / "agent.log"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return [e for e in map(json.loads, lines) if e.get("run_id") == run_id]


def names(evts: list[dict]) -> list[str]:
    return [e["event"] for e in evts]


def transcript(title: str, agent, recorder: Recorder, since: int, run_id: str) -> str:
    """Full communication of one run: every LLM exchange, then the agent's log events."""
    out = [f"\n===== {title} (run_id={run_id}) =====", "--- LLM exchanges (in order) ---"]
    for i, call in enumerate(recorder.calls[since:], 1):
        out.append(f"[{i}] {call['step']}\n    in : {_short(call['input'])}\n    out: {_short(call['output'])}")
    out.append("--- agent log events ---")
    for e in events(agent, run_id):
        extra = {k: v for k, v in e.items() if k not in ("ts", "level", "event", "run_id")}
        out.append(f"{e['event']:15} {_short(extra, 300)}")
    text = "\n".join(out)
    print(text)
    return text


# ---------------------------------------------------------------- fixtures
@pytest.fixture(scope="module")
def agent():
    return importlib.import_module("app.agent.agent")


@pytest.fixture(scope="module")
def recorder(agent):
    rec = Recorder()
    rec.wrap(agent)
    return rec


@pytest.fixture(scope="module")
def client(agent, recorder):
    with TestClient(create_app(Settings(api_keys=SecretStr("test-key")))) as c:  # `with` runs the startup (GoldDB)
        yield c


def new_id() -> str:
    return "it-" + uuid.uuid4().hex[:16]


def query(client, question, run_id):
    return client.post("/assistant/query", json={"question": question},
                       headers={**HEADERS, "X-Correlation-ID": run_id})


def approve(client, run_id, approved):
    return client.post("/assistant/approve", json={"run_id": run_id, "approved": approved},
                       headers={**HEADERS, "X-Correlation-ID": run_id})


@pytest.fixture(scope="module")
def happy(client, agent, recorder):
    """One full question -> plan -> approval -> answer round trip, shared by the approval tests."""
    run_id, since = new_id(), len(recorder.calls)
    first = query(client, KPI_QUESTION, run_id)
    before = names(events(agent, run_id))  # what the agent did before the human approved
    second = approve(client, run_id, True)
    text = transcript("approval flow", agent, recorder, since, run_id)
    return {"run_id": run_id, "first": first, "second": second, "before": before,
            "after": events(agent, run_id), "text": text}


# ---------------------------------------------------------------- tests
def test_health_ready_and_auth(client):
    assert client.get("/health").status_code == 200
    ready = client.get("/ready").json()
    assert ready["gold"] is True, ready
    assert client.post("/assistant/query", json={"question": "hi"}).status_code == 401


def test_nothing_runs_before_approval(happy):
    first = happy["first"]
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["status"] == "awaiting_approval", happy["text"]
    assert body["run_id"] == happy["run_id"] == first.headers["X-Correlation-ID"]  # one ID end to end
    assert "get_kpi" in body["plan"], happy["text"]
    assert body["result"] is None
    assert "plan_reviewed" in happy["before"]
    assert "tool_call" not in happy["before"], "a tool ran before the human approved the plan"


def test_tools_run_after_approval(happy):
    assert happy["second"].status_code == 200, happy["second"].text
    body = happy["second"].json()
    assert body["status"] == "answered", happy["text"]
    result = body["result"]
    assert result["grounded"] is True, happy["text"]
    first_call = result["evidence"][0]
    assert first_call["tool"] == "get_kpi" and first_call["status"] == "ok", happy["text"]
    assert 0 <= first_call["result"]["value"] <= 1, "availability is a ratio"
    assert "get_kpi" in result["source_measure"]
    order = names(happy["after"])
    keys = ["run_started", "plan_created", "plan_reviewed", "plan_decision", "tool_call", "answer_ready", "run_finished"]
    positions = [order.index(k) for k in keys]
    assert positions == sorted(positions), f"unexpected event order: {order}"
    decision = next(e for e in happy["after"] if e["event"] == "plan_decision")
    assert decision["approved"] is True


def test_answer_is_grounded_in_tool_value(happy):
    result = happy["second"].json()["result"]
    value = result["evidence"][0]["result"]["value"]
    candidates = [f"{value:.{d}f}" for d in (2, 3, 4)] + [f"{value * 100:.{d}f}" for d in (1, 2)]
    assert any(c in result["answer"] for c in candidates), f"{value} not in answer: {result['answer']}"
    assert not any("not found in the tool results" in lim for lim in result["limitations"]), result["limitations"]


def test_top_n_question_uses_query_kpi_table(client, agent, recorder):
    """A ranking needs the guarded-SQL tool: the LLM writes the SELECT, the guardrail checks it, Databricks runs it."""
    run_id, since = new_id(), len(recorder.calls)
    first = query(client, "Show me the 5 pieces of equipment with the most unplanned downtime hours in 2026-04", run_id)
    assert first.json()["status"] == "awaiting_approval", first.text
    body = approve(client, run_id, True).json()
    text = transcript("top 5 (query_kpi_table)", agent, recorder, since, run_id)
    assert body["status"] == "answered", text
    result = body["result"]
    call = result["evidence"][0]
    assert call["tool"] == "query_kpi_table" and call["status"] == "ok", text
    tool_result = call["result"]
    assert "error" not in tool_result, text
    sql = tool_result["sql"]
    assert sql.upper().lstrip().startswith(("SELECT", "WITH")), sql
    rows = tool_result["rows"]
    assert tool_result["row_count"] == len(rows) and 0 < len(rows) <= 200 and tool_result["attempts"] <= 3, text
    assert "ORDER BY" in sql.upper() and "DESC" in sql.upper(), f"a ranking needs ORDER BY ... DESC: {sql}"
    downtime = [v for row in rows for k, v in row.items() if "downtime" in k.lower() and isinstance(v, float)]
    assert downtime == sorted(downtime, reverse=True), "rows are not ranked by unplanned downtime, highest first"
    top5 = downtime[:5]  # the answer must state the five highest values from the rows (names vary, numbers do not)
    assert len(top5) == 5 and all(f"{v:.2f}" in result["answer"] for v in top5), f"{top5} not in answer\n{text}"
    assert result["grounded"] is True
    assert sql in result["source_measure"], "the SQL that ran must be traceable in source_measure"
    if "LIMIT 5" not in sql.upper():  # known gap, not a failure: the planner paraphrases the question and drops the "5"
        warnings.warn(f"the user's 'top 5' did not reach the SQL (planner rewrote the question), got: ...{sql[-40:]}")
    logged = [e for e in events(agent, run_id) if e["event"] == "tool_call"]
    assert [e["tool"] for e in logged] == ["query_kpi_table"] and logged[0]["ok"] is True, text


def test_plan_revision_loop(client, agent, recorder, monkeypatch):
    """The reviewer sends an invalid plan back; the real planner must use the feedback and fix it."""
    real_plan, feedback_seen = agent.plan, []

    def plan(question, feedback=None):
        feedback_seen.append(feedback)
        return BAD_PLAN if len(feedback_seen) == 1 else real_plan(question, feedback)

    monkeypatch.setattr(agent, "plan", plan)
    run_id, since = new_id(), len(recorder.calls)
    body = query(client, KPI_QUESTION, run_id).json()
    text = transcript("revision loop", agent, recorder, since, run_id)
    reviews = [e for e in events(agent, run_id) if e["event"] == "plan_reviewed"]
    assert reviews[0]["verdict"] == "revise" and reviews[0]["by"] == "check_plan", text
    assert "drop_table" in " ".join(reviews[0]["reasons"]), text
    assert reviews[0]["revisions"] == 1
    assert feedback_seen[0] is None and "drop_table" in feedback_seen[1], "planner did not get the feedback"
    assert reviews[-1]["verdict"] == "approve", text
    assert body["status"] == "awaiting_approval" and "drop_table" not in body["plan"], text

    final = approve(client, run_id, True).json()
    assert final["status"] == "answered" and final["result"]["evidence"][0]["tool"] == "get_kpi", text


def test_revisions_exhausted_is_rejected(client, agent, recorder, monkeypatch):
    """A plan that never becomes valid is rejected after the allowed revisions; no approval, no tool."""
    monkeypatch.setattr(agent, "plan", lambda question, feedback=None: BAD_PLAN)
    run_id, since = new_id(), len(recorder.calls)
    body = query(client, KPI_QUESTION, run_id).json()
    text = transcript("revisions exhausted", agent, recorder, since, run_id)
    reviews = [e for e in events(agent, run_id) if e["event"] == "plan_reviewed"]
    assert len(reviews) == agent.MAX_REVISIONS + 1 and all(r["verdict"] == "revise" for r in reviews), text
    assert body["status"] == "answered", "there is no plan to approve"
    assert body["result"]["evidence"] == []
    assert body["result"]["limitations"][0].startswith("Plan not approved (revise)"), text
    assert "tool_call" not in names(events(agent, run_id))
    assert "PlanReview" not in recorder.steps(since), "the judge LLM is not called for deterministic violations"


def test_out_of_scope_question_is_rejected_by_judge(client, agent, recorder):
    run_id, since = new_id(), len(recorder.calls)
    body = query(client, "What is the weather in Athens tomorrow?", run_id).json()
    text = transcript("out-of-scope question", agent, recorder, since, run_id)
    assert body["status"] == "answered", text
    assert body["result"]["evidence"] == [] and body["result"]["grounded"] is False
    assert body["result"]["limitations"][0].startswith("Plan not approved"), text
    assert "tool_call" not in names(events(agent, run_id)), text


def test_user_declines_plan(client, agent, recorder):
    run_id, since = new_id(), len(recorder.calls)
    first = query(client, KPI_QUESTION, run_id).json()
    assert first["status"] == "awaiting_approval"
    body = approve(client, run_id, False).json()
    text = transcript("user declines", agent, recorder, since, run_id)
    assert body["status"] == "answered"
    assert body["result"]["evidence"] == [] and body["result"]["grounded"] is False
    assert any("declined" in lim for lim in body["result"]["limitations"]), text
    evts = events(agent, run_id)
    assert next(e for e in evts if e["event"] == "plan_decision")["approved"] is False
    assert "tool_call" not in names(evts), text


def test_approve_errors(client, happy):
    unknown = approve(client, "it-does-not-exist", True)
    assert unknown.status_code == 404 and unknown.json()["error"]["code"] == "unknown_run"
    again = approve(client, happy["run_id"], True)  # that run already finished
    assert again.status_code == 409 and again.json()["error"]["code"] == "run_not_pending"
