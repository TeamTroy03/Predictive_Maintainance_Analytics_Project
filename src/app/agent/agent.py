"""Planner -> Evaluator -> Executor flow for the read-only assistant (spec 6.5)."""

import json
import logging
import math
import os
import re
import time
import uuid
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, ToolMessage
from langchain_openai import AzureChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import ValidationError

from app.agent.prompts.system_prompt import (
    EXECUTOR_PROMPT,
    JUDGE_PROMPT,
    PLANNER_PROMPT,
    SUMMARY_PROMPT,
    format_kpi_docs,
    format_tool_docs,
)
from app.agent.schemas import AssistantAnswer, Plan, PlanReview, State
from app.agent.tools.detail_tools import (
    get_schema,
    make_detail_tool,
    make_sql_writer,
    run_detail_sql,
)
from app.agent.tools.kpi_tools import KPIS, KpiRequest, get_kpi

load_dotenv()

# ---------- Logging ----------

LOG_DIR = Path(__file__).parent / ".log"  # gitignored (*.log*)


class _JsonFormatter(logging.Formatter):
    """One JSON object per line: timestamp, level, event and the event's fields (run_id, ...)."""

    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def _make_logger() -> logging.Logger:
    log = logging.getLogger("app.agent")
    if not log.handlers:  # not again on re-import
        LOG_DIR.mkdir(exist_ok=True)
        handler = RotatingFileHandler(LOG_DIR / "agent.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(_JsonFormatter())
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.propagate = False
    return log


logger = _make_logger()


def log_event(event: str, run_id: str, level: int = logging.INFO, exc_info: bool = False, **fields) -> None:
    """Write one structured line to .log/agent.log. run_id ties together every line of one question.
    Never pass secrets: only questions, plans, tool arguments, timings and verdicts are logged."""
    logger.log(level, event, extra={"fields": {"run_id": run_id, **fields}}, exc_info=exc_info)


def _since(start: float) -> float:
    return round(time.perf_counter() - start, 3)


# ---------- LLM ----------

# Every LLM call is bounded: at most (1 + LLM_MAX_RETRIES) attempts of LLM_TIMEOUT_SECONDS each. The client retries
# connection errors, timeouts, 429 and 5xx with backoff; anything else (e.g. a 400) fails at once.
LLM_TIMEOUT_SECONDS = float(os.getenv("LLM_TIMEOUT_SECONDS", "60"))
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "2"))

llm = AzureChatOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
    api_version=os.environ["OPENAI_API_VERSION"],
    timeout=LLM_TIMEOUT_SECONDS,
    max_retries=LLM_MAX_RETRIES,
)


def _ask(schema, prompt, **variables):
    """Fill the chat prompt template and get a structured (Pydantic) answer from the LLM."""
    return (prompt | llm.with_structured_output(schema, method="function_calling")).invoke(variables)


# ---------- Tools ----------

query_kpi_table = make_detail_tool(get_schema, make_sql_writer(llm), run_detail_sql)


TOOLS = {t.name: t for t in (get_kpi, query_kpi_table)}  # approved allowlist

# ---------- Prompt variables ----------

# Shared by the planner and judge templates; the template text itself lives in prompts/system_prompt.py.
TOOL_DOCS = format_tool_docs(TOOLS)
KPI_DOCS = format_kpi_docs(KPIS)

# ---------- Steps ----------


def plan_text(question: str, p: Plan) -> str:
    """Plain-language plan shown to the user. The technical steps stay in State["plan"] and the plan_created log."""
    full = f"Approach: {p.approach}\n" + "\n".join(
        f"{i}. {s.tool}({json.dumps(s.args)}) - {s.purpose}" for i, s in enumerate(p.steps, 1)
    )
    return (SUMMARY_PROMPT | llm).invoke({"question": question, "plan": full}).content


def plan(question: str, feedback: str | None = None) -> Plan:
    note = f"\n\nReviewer feedback to address: {feedback}" if feedback else ""
    return _ask(Plan, PLANNER_PROMPT, tool_docs=TOOL_DOCS, kpi_docs=KPI_DOCS, question=question, feedback_note=note)


def check_plan(p: Plan) -> list[str]:
    """Deterministic validation; returns a list of violations (empty = ok)."""
    errors = []
    for s in p.steps:
        if s.tool not in TOOLS:
            errors.append(f"Tool '{s.tool}' is not allowed.")
            continue
        schema = TOOLS[s.tool].get_input_schema().model_json_schema()
        missing = set(schema.get("required", [])) - s.args.keys()
        unknown = s.args.keys() - schema["properties"].keys()
        if missing or unknown:
            errors.append(
                f"{s.tool}: missing arguments {sorted(missing)}, unknown arguments {sorted(unknown)}."
            )
            continue
        if s.tool == "get_kpi":
            try:
                KpiRequest(**s.args)  # same validation the tool applies: kpi name, YYYY-MM months, filters
            except ValidationError as e:
                errors.append("get_kpi: " + "; ".join(f"{'.'.join(map(str, x['loc'])) or 'request'}: {x['msg']}" for x in e.errors()))
    return errors


def judge_plan(question: str, p: Plan) -> PlanReview:
    return _ask(PlanReview, JUDGE_PROMPT, tool_docs=TOOL_DOCS, question=question, plan=p.model_dump_json())


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")  # 12, 1,234.5, 0.93
# not measurements, ignored in the answer: dates/months (2026-04), times, equipment IDs (EQ-10001, CDM-B0445), years and
# list numbers ("1. Pump", only at a line start or after : ; ,)
_IDENTIFIERS = re.compile(
    r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?|\b[A-Za-z]+-[A-Za-z]*\d+\b|\b20\d{2}\b(?!\.\d)"
    r"|(?:^|(?<=[:;,]\s))\d{1,2}[.)](?=\s)",
    re.MULTILINE,
)


def _values(x) -> list[float]:
    """Every numeric value (not bool) inside a tool result."""
    if isinstance(x, bool):
        return []
    if isinstance(x, (int, float)):
        return [float(x)]
    if isinstance(x, dict):
        x = list(x.values())
    if isinstance(x, (list, tuple)):
        return [v for item in x for v in _values(item)]
    return []


def unverified_numbers(answer: AssistantAnswer, results: list) -> list[str]:
    """Numbers written in the answer or evidence that match no numeric value of a tool result. A number is backed
    when it equals a result value (or that value as a percentage, 0.931 -> 93.1) rounded or truncated to the digits
    shown. Dates, times, equipment IDs and years are ignored. Simple on purpose: a heuristic that warns, it does not
    prove the answer right (e.g. a correct number attached to the wrong KPI passes)."""
    values = [v for r in results for v in _values(r)]
    bad = []
    for text in [answer.answer, *answer.evidence]:
        for raw in _NUMBER.findall(_IDENTIFIERS.sub(" ", text)):
            x, digits = float(raw.replace(",", "")), len(raw.partition(".")[2])
            if not any(x in (round(w, digits), math.trunc(w * 10**digits) / 10**digits)
                       for v in values for w in (v, v * 100)):
                bad.append(raw)
    return list(dict.fromkeys(bad))  # unique, in order


def execute(question: str, p: Plan, run_id: str = "-") -> tuple[AssistantAnswer, list[dict]]:
    """Run the approved steps; returns the answer and the trace of every tool call (tool, args, ok, result)."""
    limitations, results, oks = [], [], []
    calls = [{"name": s.tool, "args": s.args, "id": f"step{i}"} for i, s in enumerate(p.steps)]
    tool_messages = [AIMessage(content="", tool_calls=calls)]  # ToolMessages must answer tool_calls
    for call in calls:
        start = time.perf_counter()
        try:
            result = TOOLS[call["name"]].invoke(call["args"])
            oks.append(True)
            log_event("tool_call", run_id, tool=call["name"], args=call["args"], ok=True, duration_s=_since(start))
        except Exception as e:  # tool errors are reported, not raised
            result = None
            oks.append(False)
            limitations.append(f"Tool '{call['name']}' failed: {e}")
            log_event("tool_call", run_id, logging.WARNING, tool=call["name"], args=call["args"], ok=False,
                      error=str(e), duration_s=_since(start))
        results.append(result)
        tool_messages.append(ToolMessage(json.dumps(result, default=str), tool_call_id=call["id"]))
    start = time.perf_counter()
    answer = _ask(AssistantAnswer, EXECUTOR_PROMPT, question=question, tool_messages=tool_messages)
    answer.source_measure = ", ".join(  # traceable, not LLM-written; the detail tool also shows the SQL it ran
        c["name"] + (f" [{r['sql']}]" if isinstance(r, dict) and "sql" in r else "") for c, r in zip(calls, results)
    )
    answer.limitations += limitations
    if lines := [f"{c['name']}: value={r['value']} {r.get('unit', '')}, rows={r.get('row_count')}, as of {r.get('as_of_ts')}"
                 for c, r in zip(calls, results) if isinstance(r, dict) and "value" in r]:
        answer.evidence = lines  # exact figures from the tools, not LLM-written
    if bad := unverified_numbers(answer, results):
        answer.limitations.append(f"These numbers were not found in the tool results, please verify: {', '.join(bad)}.")
    log_event("answer_ready", run_id, unverified_numbers=bad, limitations=answer.limitations,
              source_measure=answer.source_measure, filters=answer.filters, duration_s=_since(start))
    trace = [{"tool": c["name"], "args": c["args"], "ok": ok, "result": r} for c, ok, r in zip(calls, oks, results)]
    return answer, trace


def _refuse(reason: str) -> AssistantAnswer:
    return AssistantAnswer(answer="", evidence=[], filters={}, source_measure="", limitations=[reason])


# ---------- Graph ----------

MAX_REVISIONS = 2


def planner_node(s: State) -> State:
    start = time.perf_counter()
    p = plan(s["question"], s.get("feedback"))
    log_event("plan_created", s["run_id"], revision=s.get("revisions", 0), approach=p.approach,
              steps=[step.model_dump() for step in p.steps], duration_s=_since(start))
    return {"plan": p}


def evaluator_node(s: State) -> State:
    start = time.perf_counter()
    errors = check_plan(s["plan"])
    if errors:  # deterministic violation: revise without calling the LLM
        review = PlanReview(verdict="revise", reasons=errors, feedback_for_planner=" ".join(errors))
    else:
        review = judge_plan(s["question"], s["plan"])
    revisions = s.get("revisions", 0) + (review.verdict == "revise")
    log_event("plan_reviewed", s["run_id"], verdict=review.verdict, reasons=review.reasons,
              by="check_plan" if errors else "judge", revisions=revisions, duration_s=_since(start))
    out = {
        "verdict": review.verdict,
        "reasons": review.reasons,
        "feedback": review.feedback_for_planner,
        "revisions": revisions,
    }
    if review.verdict == "approve":  # built here so the LLM summary is not re-run on resume
        out["plan_text"] = plan_text(s["question"], s["plan"])
    return out


def route_after_evaluator(s: State) -> str:
    if s["verdict"] == "approve":
        return "approval"
    if s["verdict"] == "revise" and s["revisions"] <= MAX_REVISIONS:
        return "planner"
    return "executor"  # rejected or revisions exhausted


def approval_node(s: State) -> State:
    """Pause until the user approves or declines the plan."""
    if interrupt(s["plan_text"]):
        return {}
    return {"verdict": "declined", "reasons": ["The user declined the plan."]}


def executor_node(s: State) -> State:
    if s["verdict"] != "approve":  # nothing is executed
        log_event("not_executed", s["run_id"], verdict=s["verdict"], reasons=s["reasons"])
        return {"answer": _refuse(f"Plan not approved ({s['verdict']}): {' '.join(s['reasons'])}")}
    answer, trace = execute(s["question"], s["plan"], s["run_id"])
    return {"answer": answer, "trace": trace}


_graph = StateGraph(State)
_graph.add_node("planner", planner_node)
_graph.add_node("evaluator", evaluator_node)
_graph.add_node("approval", approval_node)
_graph.add_node("executor", executor_node)
_graph.add_edge(START, "planner")
_graph.add_edge("planner", "evaluator")
_graph.add_conditional_edges("evaluator", route_after_evaluator, ["planner", "approval", "executor"])
_graph.add_edge("approval", "executor")
_graph.add_edge("executor", END)
app = _graph.compile(checkpointer=MemorySaver())  # checkpointer is required for interrupt()


def ask_on_console(text: str) -> bool:
    return input(f"\n{text}\n\nApprove this plan? [y/N] ").strip().lower() == "y"


def _config(run_id: str) -> dict:
    return {"configurable": {"thread_id": run_id}}  # the run_id is the LangGraph thread_id


def run_exists(run_id: str) -> bool:
    """True if a run with this ID already has a checkpoint (pending or finished)."""
    return bool(app.get_state(_config(run_id)).values)


def pending_plan(run_id: str) -> str | None:
    """The plan text if the run is paused at the approval step, else None (unknown or already finished)."""
    state = app.get_state(_config(run_id))
    return state.values.get("plan_text") if state.next == ("approval",) else None


def start_run(question: str, run_id: str | None = None) -> tuple[str, str | None, dict]:
    """Run until the approval step. Returns (run_id, plan_text, state): plan_text is the plan waiting for
    approve/decline, or None when the run already finished (e.g. rejected plan) and state has the answer.
    run_id lets the caller reuse its own correlation ID; it must not belong to an existing run."""
    run_id = run_id or uuid.uuid4().hex
    start = time.perf_counter()
    log_event("run_started", run_id, question=question)
    try:
        result = app.invoke({"question": question, "run_id": run_id}, _config(run_id))
    except Exception as e:  # e.g. the LLM still failing after its bounded retries
        log_event("run_failed", run_id, logging.ERROR, exc_info=True, error=str(e), duration_s=_since(start))
        raise
    if "__interrupt__" in result:
        return run_id, result["__interrupt__"][0].value, result
    log_event("run_finished", run_id, verdict=result.get("verdict"), duration_s=_since(start))
    return run_id, None, result


def finish_run(run_id: str, approved: bool) -> dict:
    """Resume a paused run with the user's decision; returns the final state (answer, trace)."""
    start = time.perf_counter()
    log_event("plan_decision", run_id, approved=bool(approved))
    try:
        result = app.invoke(Command(resume=approved), _config(run_id))
    except Exception as e:
        log_event("run_failed", run_id, logging.ERROR, exc_info=True, error=str(e), duration_s=_since(start))
        raise
    log_event("run_finished", run_id, verdict=result.get("verdict"), duration_s=_since(start))
    return result


def run(question: str, approve=ask_on_console) -> AssistantAnswer:
    run_id, plan_text, result = start_run(question)
    if plan_text is not None:  # waiting for the user's decision
        result = finish_run(run_id, approve(plan_text))
    return result["answer"]