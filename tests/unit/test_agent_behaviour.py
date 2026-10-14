"""Agent behaviour with a fake LLM and fake Databricks: plan validation, the approval gate, tool execution, SQL loop.
Nothing leaves the machine: dummy Azure settings, `plan` / `judge_plan` / `plan_text` / `_ask` replaced, tools spied."""

from types import SimpleNamespace

import pytest

from app.agent.memory import ConversationMemory
from pydantic import ValidationError

from app.agent.prompts.system_prompt import PLANNER_PROMPT
from app.agent.schemas import MAX_PLAN_STEPS, AssistantAnswer, Plan, PlanReview, PlanStep
from app.agent.tools import detail_tools as dt
from app.agent.tools import kpi_tools
from app.agent.tools.kpi_tools import KpiRequest, compute_kpi

GET_KPI = {"kpi_name": "availability", "start_month": "2026-01", "end_month": "2026-03"}
KPI_RESULT = {"kpi_name": "availability", "value": 0.93, "unit": "ratio (0-1)", "row_count": 12, "as_of_ts": "2026-09-21"}


def make_plan(*steps: tuple[str, dict]) -> Plan:
    return Plan(approach="look it up", steps=[PlanStep(tool=t, args=a, purpose="because") for t, a in steps])


# ---------------------------------------------------------------- hermetic import of the agent
@pytest.fixture(scope="module")
def env(tmp_path_factory):
    """Dummy settings set before app.agent.agent is imported (it builds AzureChatOpenAI and calls load_dotenv, which
    never overrides a variable that is already set, so a developer's .env cannot leak in)."""
    values = {
        "AZURE_OPENAI_ENDPOINT": "https://example.invalid", "AZURE_OPENAI_API_KEY": "dummy",
        "AZURE_OPENAI_DEPLOYMENT_NAME": "dummy", "OPENAI_API_VERSION": "2024-12-01-preview",
        "GOLD_CATALOG": "data2026acc123456", "GOLD_SCHEMA": "team3", "GOLD_TABLE_PREFIX": "test_",
        "DATABRICKS_HOST": "example.invalid", "DATABRICKS_HTTP_PATH": "/sql/none", "DATABRICKS_TOKEN": "dummy",
        "AGENT_LOG_DIR": str(tmp_path_factory.mktemp("agent-log")),
    }
    with pytest.MonkeyPatch.context() as mp:
        for k, v in values.items():
            mp.setenv(k, v)
        yield


@pytest.fixture(scope="module")
def ag(env):
    from app.agent import agent

    return agent


# ---------------------------------------------------------------- check_plan
@pytest.mark.parametrize("step, fragment", [
    (("run_sql", {"query": "SELECT 1"}), "Tool 'run_sql' is not allowed"),
    (("get_kpi", {"kpi_name": "availability"}), "missing arguments"),
    (("get_kpi", {**GET_KPI, "colour": "red"}), "unknown arguments ['colour']"),
    (("get_kpi", {**GET_KPI, "kpi_name": "profit"}), "get_kpi: kpi_name"),
    (("get_kpi", {**GET_KPI, "start_month": "2026-13"}), "get_kpi: start_month"),
    (("get_kpi", {**GET_KPI, "start_month": "2026-05", "end_month": "2026-01"}), "end_month must not be before"),
    (("get_kpi", {**GET_KPI, "equipment_id": "EQ'; DROP TABLE x"}), "get_kpi: equipment_id"),
    (("query_kpi_table", {}), "missing arguments ['question']"),
])
def test_check_plan_reports_violations(ag, step, fragment):
    errors = ag.check_plan(make_plan(step))
    assert errors and fragment in " ".join(errors)


def test_check_plan_accepts_the_approved_tools(ag):
    plan = make_plan(("get_kpi", {**GET_KPI, "equipment_type": "Pump"}), ("query_kpi_table", {"question": "top 5 sites"}))
    assert ag.check_plan(plan) == []
    assert set(ag.TOOLS) == {"get_kpi", "query_kpi_table", "query_graph"}  # the allowlist itself


def test_check_plan_reports_every_bad_step(ag):
    plan = make_plan(("run_sql", {}), ("get_kpi", {"kpi_name": "availability"}))
    assert len(ag.check_plan(plan)) == 2


# ---------------------------------------------------------------- plan size
def test_a_plan_has_at_most_seven_steps():
    assert MAX_PLAN_STEPS == 7
    assert len(make_plan(*[("get_kpi", GET_KPI)] * MAX_PLAN_STEPS).steps) == 7
    with pytest.raises(ValidationError):
        make_plan(*[("get_kpi", GET_KPI)] * (MAX_PLAN_STEPS + 1))


def test_the_planner_prompt_quotes_the_same_limit():
    system = PLANNER_PROMPT.format_messages(today="2026-10-15", data_note="", history="", tool_docs="", kpi_docs="",
                                            question="q", feedback_note="")[0].content
    assert f"at most {MAX_PLAN_STEPS} steps" in system


# ---------------------------------------------------------------- unverified_numbers
def answer(text, evidence=()):
    return AssistantAnswer(answer=text, evidence=list(evidence), filters={}, source_measure="", limitations=[])


def test_numbers_backed_by_a_tool_result_pass(ag):
    results = [{"value": 0.931, "row_count": 12}]
    # 0.93 rounded, 93.1 as a percentage, 12 from row_count; the date, the equipment id and the year are ignored
    text = "EQ-10002 had 0.93 (93.1 %) over 12 rows in 2026-04, as of 2026."
    assert ag.unverified_numbers(answer(text), results) == []


def test_invented_numbers_are_reported_once_in_order(ag):
    results = [{"value": 0.93}]
    assert ag.unverified_numbers(answer("It was 77 and 12.5, then 77 again", ["also 0.93"]), results) == ["77", "12.5"]


# ---------------------------------------------------------------- graph flow
@pytest.fixture
def flow(ag, monkeypatch):
    """Fake LLM steps and spied tools. Set flow.plan / flow.verdict / flow.answer before start_run."""
    f = SimpleNamespace(
        plan=make_plan(("get_kpi", GET_KPI)), verdict="approve", reasons=["fine"],
        answer=answer("Availability was 0.93.", ["x"]),
        planner_calls=[], judge_calls=0, kpi_calls=[], sql_calls=[], executor_inputs=[],
    )

    def fake_plan(question, feedback=None, history=""):
        f.planner_calls.append({"question": question, "feedback": feedback, "history": history})
        return f.plan

    def fake_judge(question, p, history=""):
        f.judge_calls += 1
        return PlanReview(verdict=f.verdict, reasons=f.reasons, feedback_for_planner="try again")

    def fake_ask(schema, prompt, **variables):  # only the executor's AssistantAnswer is asked in these tests
        assert schema is AssistantAnswer
        f.executor_inputs.append(variables)
        return f.answer.model_copy(deep=True)

    def fake_compute(request):
        f.kpi_calls.append(request)
        return {**KPI_RESULT, "limitations": []}

    def fake_run_sql(sql):
        f.sql_calls.append(sql)
        return [{"site": "A", "mttr_hours": 2.5}]

    detail = dt.make_detail_tool(lambda: {"kpi_table": {"columns": [{"name": "site", "type": "string"}]}},
                                 lambda q, schema, attempts: "SELECT site FROM kpi_table", fake_run_sql)
    monkeypatch.setattr(ag, "plan", fake_plan)
    monkeypatch.setattr(ag, "judge_plan", fake_judge)
    monkeypatch.setattr(ag, "plan_text", lambda question, p: "PLAN TEXT")
    monkeypatch.setattr(ag, "_ask", fake_ask)
    monkeypatch.setattr(kpi_tools, "compute_kpi", fake_compute)  # get_kpi calls it through the module global
    monkeypatch.setitem(ag.TOOLS, "query_kpi_table", detail)
    monkeypatch.setattr(ag, "memory", ConversationMemory())  # per test, nothing shared between them
    return f


def test_nothing_runs_before_approval(ag, flow):
    run_id, plan_text, state = ag.start_run("What was the availability?")
    assert plan_text == "PLAN TEXT" and "__interrupt__" in state
    assert flow.kpi_calls == [] and flow.sql_calls == [] and flow.executor_inputs == []
    assert ag.pending_plan(run_id) == "PLAN TEXT"
    assert flow.judge_calls == 1  # the deterministic check passed, the judge reviewed it


def test_approval_resumes_and_runs_the_plan_once(ag, flow):
    run_id, _, _ = ag.start_run("What was the availability?")
    result = ag.finish_run(run_id, True)

    assert [c.kpi_name for c in flow.kpi_calls] == ["availability"]
    final = result["answer"]
    assert isinstance(final, AssistantAnswer) and final.answer == "Availability was 0.93."
    assert final.source_measure == "get_kpi"  # written by the code, not the LLM
    assert final.evidence == ["get_kpi: value=0.93 ratio (0-1), rows=12, as of 2026-09-21"]  # exact tool figures
    assert final.limitations == []  # every number in the answer is backed
    assert result["trace"] == [{"tool": "get_kpi", "args": GET_KPI, "ok": True, "result": {**KPI_RESULT, "limitations": []}}]


def test_a_second_approve_finds_nothing_pending(ag, flow):
    """The API answers 409 when pending_plan() is None: a finished run must not look pending."""
    run_id, _, _ = ag.start_run("What was the availability?")
    ag.finish_run(run_id, True)
    assert ag.run_exists(run_id) and ag.pending_plan(run_id) is None
    assert len(flow.kpi_calls) == 1


def test_declining_executes_nothing(ag, flow):
    run_id, _, _ = ag.start_run("What was the availability?")
    result = ag.finish_run(run_id, False)
    assert flow.kpi_calls == [] and flow.executor_inputs == []
    assert result["verdict"] == "declined" and result["answer"].answer == ""
    assert "declined" in " ".join(result["answer"].limitations)
    assert "trace" not in result


def test_a_judge_rejection_never_reaches_approval_or_tools(ag, flow):
    """Out-of-scope question: the judge rejects, the user is never asked, no tool runs, the reason is a limitation."""
    flow.verdict, flow.reasons = "reject", ["Weather is out of scope."]
    run_id, plan_text, state = ag.start_run("What is the weather?")
    assert plan_text is None and ag.pending_plan(run_id) is None
    assert flow.kpi_calls == [] and flow.sql_calls == [] and flow.executor_inputs == []
    assert state["answer"].answer == ""
    assert state["answer"].limitations == ["Plan not approved (reject): Weather is out of scope."]


def test_a_disallowed_tool_is_revised_until_the_limit_without_the_judge(ag, flow):
    flow.plan = make_plan(("run_sql", {"query": "DROP TABLE x"}))
    run_id, plan_text, state = ag.start_run("Delete everything")

    assert len(flow.planner_calls) == ag.MAX_REVISIONS + 1  # first try + MAX_REVISIONS rewrites
    assert flow.planner_calls[0]["feedback"] is None
    assert "not allowed" in flow.planner_calls[1]["feedback"]  # the violation goes back to the planner
    assert flow.judge_calls == 0  # deterministic violations never reach the judge LLM
    assert plan_text is None and flow.kpi_calls == [] and flow.sql_calls == []
    assert state["answer"].limitations[0].startswith("Plan not approved (revise)")


def test_a_bad_plan_is_corrected_by_the_planner(ag, flow, monkeypatch):
    bad, good = make_plan(("get_kpi", {"kpi_name": "availability"})), make_plan(("get_kpi", GET_KPI))
    plans = iter([bad, good])
    monkeypatch.setattr(ag, "plan", lambda question, feedback=None, history="": next(plans))
    _, plan_text, _ = ag.start_run("What was the availability?")
    assert plan_text == "PLAN TEXT" and flow.kpi_calls == []  # fixed plan is waiting for approval, not run


def test_the_detail_tool_runs_after_approval_with_the_row_cap(ag, flow):
    flow.plan = make_plan(("query_kpi_table", {"question": "which sites"}))
    run_id, _, _ = ag.start_run("Which sites?")
    assert flow.sql_calls == []
    result = ag.finish_run(run_id, True)
    assert flow.sql_calls == ["SELECT site FROM kpi_table LIMIT 200"]
    assert result["trace"][0]["result"]["row_count"] == 1
    assert "[SELECT site FROM kpi_table LIMIT 200]" in result["answer"].source_measure  # the SQL is shown


def test_a_failing_tool_is_reported_as_a_limitation_not_raised(ag, flow, monkeypatch):
    def boom(request):
        raise RuntimeError("warehouse down")

    monkeypatch.setattr(kpi_tools, "compute_kpi", boom)
    run_id, _, _ = ag.start_run("What was the availability?")
    result = ag.finish_run(run_id, True)
    assert result["trace"][0]["ok"] is False and result["trace"][0]["result"] is None
    assert "Tool 'get_kpi' failed: warehouse down" in result["answer"].limitations


def test_malformed_tool_arguments_come_back_as_an_error_result(ag, flow):
    """If a bad plan slipped past check_plan, get_kpi still refuses it and the query never runs."""
    _, trace = ag.execute("q", make_plan(("get_kpi", {**GET_KPI, "start_month": "2026-99"})))
    assert trace[0]["result"]["error"].startswith("Invalid KPI request") and flow.kpi_calls == []


def test_numbers_the_llm_invents_are_flagged(ag, flow):
    flow.answer = answer("Availability was 0.93 but 77 sensors failed.")
    run_id, _, _ = ag.start_run("What was the availability?")
    limitations = ag.finish_run(run_id, True)["answer"].limitations
    assert limitations == ["These numbers were not found in the tool results, please verify: 77."]


def test_follow_up_questions_get_the_earlier_turn_through_conversation_id(ag, flow):
    run_id, _, _ = ag.start_run("Availability of pumps?", conversation_id="c1")
    ag.finish_run(run_id, True)
    ag.start_run("And for compressors?", conversation_id="c1")
    ag.start_run("Unrelated question", conversation_id="other")

    first, follow_up, other = flow.planner_calls
    assert first["history"] == ""
    assert "Availability of pumps?" in follow_up["history"] and "Availability was 0.93." in follow_up["history"]
    assert other["history"] == ""  # conversations do not leak into each other


def test_declined_questions_are_not_remembered(ag, flow):
    run_id, _, _ = ag.start_run("Availability?", conversation_id="c1")
    ag.finish_run(run_id, False)
    assert ag.memory.history("c1") == []


# ---------------------------------------------------------------- compute_kpi with a fake database
def kpi_request(**extra):
    return KpiRequest(kpi_name="mttr_hours", start_month="2026-01", end_month="2026-03", **extra)


def fake_row(**overrides):
    return {"value": 2.5, "row_count": 4, "as_of_ts": "2026-09-21", "gold_loaded_ts": "2026-09-22", **overrides}


def test_compute_kpi_builds_governed_sql_with_bound_parameters(env):
    seen = {}

    def run(sql, params):
        seen.update(sql=sql, params=params)
        return fake_row()

    result = compute_kpi(kpi_request(equipment_id="eq-10002", equipment_type="Pump"), run=run)

    assert seen["params"] == {"start": 20260101, "end": 20260301, "equipment_type": "Pump", "equipment_id": "eq-10002"}
    sql = seen["sql"]
    assert "SUM(unplanned_downtime_hours) / NULLIF(SUM(unplanned_failure_count), 0) AS value" in sql
    assert "data2026acc123456.team3.test_gold_kpi_equipment_month" in sql
    assert "equipment_key <> -1" in sql  # unknown members never count
    assert "eq-10002" not in sql and "Pump" not in sql  # values only travel as parameters
    assert result["value"] == 2.5 and result["unit"] == "hours" and result["row_count"] == 4
    assert result["filters"] == {"start_month": "2026-01", "end_month": "2026-03",
                                 "equipment_type": "Pump", "equipment_id": "eq-10002"}


def test_compute_kpi_says_so_when_nothing_matches_or_the_denominator_is_zero(env):
    empty = compute_kpi(kpi_request(), run=lambda sql, params: fake_row(value=None, row_count=0, as_of_ts=None,
                                                                         gold_loaded_ts=None))
    assert empty["value"] is None and empty["as_of_ts"] is None
    assert "No Gold rows match" in empty["limitations"][-1]

    undefined = compute_kpi(kpi_request(), run=lambda sql, params: fake_row(value=None))
    assert "denominator is zero" in undefined["limitations"][-1]


# ---------------------------------------------------------------- last date of data
@pytest.fixture
def clock(monkeypatch):
    """A fake clock for data_available_until's cache, and an empty cache before and after each test."""
    now = [1000.0]
    monkeypatch.setattr(kpi_tools, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setitem(kpi_tools._data_end, "info", None)
    monkeypatch.setitem(kpi_tools._data_end, "valid_until", 0.0)
    return now


def test_data_available_until_reads_the_last_date_from_the_kpi_table(env, clock):
    seen = []

    def run(sql, params):
        seen.append((sql, params))
        return {"as_of_ts": "2026-09-21 12:30:00", "last_month_key": 20260901}

    assert kpi_tools.data_available_until(run=run) == {"as_of": "2026-09-21", "last_month": "2026-09"}
    sql, params = seen[0]
    assert "MAX(as_of_ts)" in sql and "data2026acc123456.team3.test_gold_kpi_equipment_month" in sql and params == {}


def test_data_available_until_is_cached_then_refreshed(env, clock):
    calls = []

    def run(sql, params):
        calls.append(1)
        return {"as_of_ts": "2026-09-21", "last_month_key": 20260901}

    kpi_tools.data_available_until(run=run)
    clock[0] += kpi_tools.DATA_DATE_CACHE_SECONDS - 1
    kpi_tools.data_available_until(run=run)
    assert len(calls) == 1
    clock[0] += 2
    kpi_tools.data_available_until(run=run)
    assert len(calls) == 2


def test_data_available_until_never_raises_and_does_not_hammer_a_broken_database(env, clock):
    calls = []

    def broken(sql, params):
        calls.append(1)
        raise ConnectionError("databricks down")

    assert kpi_tools.data_available_until(run=broken) is None
    assert kpi_tools.data_available_until(run=broken) is None  # inside the retry wait: not asked again
    assert len(calls) == 1
    clock[0] += kpi_tools.DATA_DATE_RETRY_SECONDS + 1
    assert kpi_tools.data_available_until(run=lambda sql, params: {"as_of_ts": "2026-09-21",
                                                                    "last_month_key": 20260901}) is not None


def test_an_empty_kpi_table_gives_no_date(env, clock):
    assert kpi_tools.data_available_until(run=lambda sql, params: {"as_of_ts": None, "last_month_key": None}) is None


def test_the_planner_and_judge_are_told_where_the_data_ends(ag, monkeypatch):
    monkeypatch.setattr(ag, "data_available_until", lambda: {"as_of": "2026-09-21", "last_month": "2026-09"})
    assert "ends on 2026-09-21" in ag._data_note() and "2026-09" in ag._data_note()
    seen = []
    monkeypatch.setattr(ag, "_ask", lambda schema, prompt, **variables: seen.append(variables) or make_plan())
    ag.plan("availability now?")
    ag.judge_plan("availability now?", make_plan())
    assert len(seen) == 2 and all("ends on 2026-09-21" in v["data_note"] for v in seen)
    monkeypatch.setattr(ag, "data_available_until", lambda: None)
    assert ag._data_note() == ""


# ---------------------------------------------------------------- SQL loop and Databricks seam
def test_run_detail_sql_rewrites_the_table_names_to_gold_tables(env, monkeypatch):
    seen = []
    monkeypatch.setattr(dt, "run_rows", lambda sql: seen.append(sql) or [])
    dt.run_detail_sql("SELECT s.site_name FROM kpi_table k JOIN dim_site s ON k.site_key = s.site_key")
    assert "data2026acc123456.team3.test_gold_kpi_equipment_month" in seen[0]
    assert "data2026acc123456.team3.test_gold_dim_site" in seen[0]


@pytest.mark.parametrize("written, expected_tail", [
    ("SELECT * FROM kpi_table", "LIMIT 200"),
    ("SELECT * FROM kpi_table LIMIT 5000", "LIMIT 200"),
    ("SELECT * FROM kpi_table LIMIT 5", "LIMIT 5"),
])
def test_queries_are_capped_at_200_rows_before_they_run(written, expected_tail):
    ran = []
    out = dt.query_with_retries("q", lambda: {}, lambda q, s, a: written, lambda sql: ran.append(sql) or [{"x": 1}])
    assert ran == [out["sql"]] and out["sql"].endswith(expected_tail) and out["attempts"] == 1


def test_a_rejected_query_is_never_run_and_gives_up_after_three_attempts():
    ran, seen_attempts = [], []

    def writer(question, schema, attempts):
        seen_attempts.append(len(attempts))
        return "DROP TABLE kpi_table"

    out = dt.query_with_retries("q", lambda: {}, writer, lambda sql: ran.append(sql))
    assert ran == [] and seen_attempts == [0, 1, 2]  # each retry sees the earlier failures
    assert out["error"] == "No valid query after 3 attempts." and len(out["attempts"]) == 3
    assert "Guardrail rejected query" in out["attempts"][0]["error"]


def test_a_database_error_goes_back_to_the_writer_and_the_retry_succeeds():
    calls = []

    def run(sql):
        calls.append(sql)
        if len(calls) == 1:
            raise RuntimeError("column nope not found")
        return [{"site": "A"}]

    writers = []

    def writer(question, schema, attempts):
        writers.append(attempts)
        return "SELECT site FROM kpi_table"

    out = dt.query_with_retries("q", lambda: {}, writer, run)
    assert out["attempts"] == 2 and out["rows"] == [{"site": "A"}]
    assert "column nope not found" in writers[1][0]["error"]