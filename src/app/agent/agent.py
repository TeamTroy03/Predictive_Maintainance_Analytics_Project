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
from typing import Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import AzureChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field, ValidationError

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


# ---------- Models ----------


class PlanStep(BaseModel):
    tool: str
    args: dict
    purpose: str


class Plan(BaseModel):
    approach: str
    steps: list[PlanStep] = Field(max_length=4)


class PlanReview(BaseModel):
    verdict: Literal["approve", "revise", "reject"]
    reasons: list[str]
    feedback_for_planner: str


class AssistantAnswer(BaseModel):
    answer: str
    evidence: list[str]
    filters: dict
    source_measure: str
    limitations: list[str]


# ---------- Tools (placeholders) ----------

@tool
def get_sql_query(equipment_id: str) -> dict:
    """Return metadata for one piece of equipment."""
    # TODO: replace with Gold/semantic layer call
    return {"equipment_id": equipment_id}


@tool
def get_mock_equipment_status(equipment_id: str, site_id: str | None = None) -> dict:
    """Return the current status, last maintenance date, number of open work orders and
    30-day alarm count of one piece of equipment."""
    # MOCK: hardcoded test data, remove once the real tools exist
    return {
        "equipment_id": equipment_id,
        "site_id": site_id or "site1",
        "status": "running",
        "last_maintenance": "2026-09-15",
        "open_work_orders": 2,
        "alarm_count_30d": 7,
    }


TOOLS = {t.name: t for t in (get_kpi, get_sql_query, get_mock_equipment_status)}  # approved allowlist

# ---------- Prompts ----------

TOOL_DOCS = "\n".join(
    f"- {t.name}: {' '.join(t.description.split())} Arguments: {json.dumps(t.args)}"
    for t in TOOLS.values()
)

PLANNER_PROMPT = (
    "You plan read-only analytics for an industrial predictive-maintenance assistant. "
    "Return a plan of at most 4 steps using ONLY these tools:\n"
    f"{TOOL_DOCS}\n"
    "Valid kpi_name values with their meaning:\n"
    + "\n".join(f"  {name}: {k['definition']} ({k['unit']})" for name, k in KPIS.items())
    + "\nKPI periods are whole months written YYYY-MM (start_month and end_month, both inclusive). "
    "Use the most relevant tool(s) for the question and do not add steps it does not need. "
    "Use the exact argument names above. Do not execute anything and do not invent data or arguments."
)
JUDGE_PROMPT = (
    "You review a plan for a read-only maintenance assistant. These are the only approved tools "
    f"and what they do:\n{TOOL_DOCS}\n"
    "Approve only if the plan answers the question using these tools (a tool is suitable if its "
    "description covers what the question asks), stays read-only and invents no data. Otherwise "
    "return 'revise' with feedback_for_planner, or 'reject' if the question cannot be answered."
)
SUMMARY_PROMPT = (
    "Summarize this analysis plan for a business user in 2-3 sentences. Describe only what "
    "will be looked up and why, in business terms. No tool names, parameters or technical terms."
)
EXECUTOR_PROMPT = (
    "Answer the question using ONLY the tool results provided. Never invent numbers. "
    "Fill answer, evidence, filters, source_measure and limitations. "
    "Mention missing or failed data in limitations."
)

# ---------- Steps ----------

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


def _ask(schema, system: str, human: str):
    structured = llm.with_structured_output(schema, method="function_calling")
    return structured.invoke([SystemMessage(system), HumanMessage(human)])


def plan_text(question: str, p: Plan) -> str:
    """Full plan text, then a plain-language summary for the business user."""
    full = f"Approach: {p.approach}\n" + "\n".join(
        f"{i}. {s.tool}({json.dumps(s.args)}) - {s.purpose}" for i, s in enumerate(p.steps, 1)
    )
    summary = llm.invoke([
        SystemMessage(SUMMARY_PROMPT),
        HumanMessage(f"Question: {question}\n\nPlan:\n{full}"),
    ]).content
    return f"{full}\n-------------\n{summary}"


def plan(question: str, feedback: str | None = None) -> Plan:
    human = question if not feedback else f"{question}\n\nReviewer feedback to address: {feedback}"
    return _ask(Plan, PLANNER_PROMPT, human)


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
    return _ask(PlanReview, JUDGE_PROMPT, f"Question: {question}\n\nPlan: {p.model_dump_json()}")


_NUMBER = re.compile(r"\d[\d,]*(?:\.\d+)?")  # 12, 1,234.5, 0.93
# not measurements, ignored in the answer: dates/months (2026-04), times, equipment IDs (EQ-10001) and years
_IDENTIFIERS = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{1,2}:\d{2}(?::\d{2}(?:\.\d+)?)?|\b[A-Za-z]+-\d+\b|\b20\d{2}\b(?!\.\d)")


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


def execute(question: str, p: Plan, run_id: str = "-") -> AssistantAnswer:
    limitations, results = [], []
    messages = [SystemMessage(EXECUTOR_PROMPT), HumanMessage(question)]
    calls = [{"name": s.tool, "args": s.args, "id": f"step{i}"} for i, s in enumerate(p.steps)]
    messages.append(AIMessage(content="", tool_calls=calls))  # ToolMessages must answer tool_calls
    for call in calls:
        start = time.perf_counter()
        try:
            result = TOOLS[call["name"]].invoke(call["args"])
            log_event("tool_call", run_id, tool=call["name"], args=call["args"], ok=True, duration_s=_since(start))
        except Exception as e:  # tool errors are reported, not raised
            result = None
            limitations.append(f"Tool '{call['name']}' failed: {e}")
            log_event("tool_call", run_id, logging.WARNING, tool=call["name"], args=call["args"], ok=False,
                      error=str(e), duration_s=_since(start))
        results.append(result)
        messages.append(ToolMessage(json.dumps(result, default=str), tool_call_id=call["id"]))
    start = time.perf_counter()
    answer = llm.with_structured_output(AssistantAnswer, method="function_calling").invoke(messages)
    answer.source_measure = ", ".join(c["name"] for c in calls)  # traceable, not LLM-written
    answer.limitations += limitations
    if lines := [f"{c['name']}: value={r['value']} {r.get('unit', '')}, rows={r.get('row_count')}, as of {r.get('as_of_ts')}"
                 for c, r in zip(calls, results) if isinstance(r, dict) and "value" in r]:
        answer.evidence = lines  # exact figures from the tools, not LLM-written
    if bad := unverified_numbers(answer, results):
        answer.limitations.append(f"These numbers were not found in the tool results, please verify: {', '.join(bad)}.")
    log_event("answer_ready", run_id, unverified_numbers=bad, limitations=len(answer.limitations),
              duration_s=_since(start))
    return answer


def _refuse(reason: str) -> AssistantAnswer:
    return AssistantAnswer(answer="", evidence=[], filters={}, source_measure="", limitations=[reason])


# ---------- Graph ----------

MAX_REVISIONS = 2


class State(TypedDict, total=False):
    question: str
    run_id: str  # correlation ID of this question, on every log line
    plan: Plan
    feedback: str | None
    revisions: int
    verdict: str  # approve | revise | reject | declined
    reasons: list[str]
    plan_text: str  # full plan + business summary shown to the user
    answer: AssistantAnswer


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
    return {"answer": execute(s["question"], s["plan"], s["run_id"])}


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


def run(question: str, approve=ask_on_console) -> AssistantAnswer:
    run_id = uuid.uuid4().hex
    config = {"configurable": {"thread_id": run_id}}
    start = time.perf_counter()
    log_event("run_started", run_id, question=question)
    try:
        result = app.invoke({"question": question, "run_id": run_id}, config)
        if "__interrupt__" in result:  # waiting for the user's decision
            approved = approve(result["__interrupt__"][0].value)
            log_event("plan_decision", run_id, approved=bool(approved))
            result = app.invoke(Command(resume=approved), config)
    except Exception as e:  # e.g. the LLM still failing after its bounded retries
        log_event("run_failed", run_id, logging.ERROR, exc_info=True, error=str(e), duration_s=_since(start))
        raise
    log_event("run_finished", run_id, verdict=result.get("verdict"), duration_s=_since(start))
    return result["answer"]


EXAMPLE_QUESTIONS = [
    # one KPI, whole fleet, one month range -> get_kpi(kpi_name="availability", ...)
    "What was the fleet availability between 2026-04 and 2026-06?",
    # KPI + equipment type filter
    "What is the MTTR of the Centrifugal Pump equipment from 2026-03 to 2026-09?",
    # KPI + equipment filter, single month
    "How many unplanned failures did equipment EQ-10001 have in 2026-07?",
]


if __name__ == "__main__":
    for question in EXAMPLE_QUESTIONS:
        print(f"\n=== {question}")
        print(run(question).answer)