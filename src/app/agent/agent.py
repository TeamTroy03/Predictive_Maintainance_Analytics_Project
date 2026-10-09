"""Planner -> Evaluator -> Executor flow for the read-only assistant (spec 6.5)."""

# TODO (not implemented yet):
# - tests (check_plan, run loop with mocked LLM)
# - timeouts and bounded retries on LLM calls
# - structured logging and correlation IDs
# - prompt-injection guarding of the user question
# - verify numbers in the final answer match the tool results
# - Azure OpenAI settings in core/config.py (LLM_API_VERSION missing from .env.example)
# - wire run() into services/assistant_service.py and /assistant/query
# - source of allowed_sites (auth/JWT)

import json
import os
from datetime import date
from typing import Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import AzureChatOpenAI
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

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

VALID_KPIS = {
    "asset_availability", "unplanned_downtime", "mtbf", "mttr",
    "overdue_work_orders", "alarm_rate", "maintenance_cost", "repeat_failure_rate",
}


@tool
def get_kpi(kpi_name: str, start_date: str, end_date: str,
            site_id: str | None = None, equipment_type: str | None = None) -> dict:
    """Return one governed KPI value for a period (dates are YYYY-MM-DD)."""
    # TODO: replace with Gold/semantic layer call
    return {"kpi_name": kpi_name, "value": None}


@tool
def get_sql_query(equipment_id: str) -> dict:
    """Return metadata for one piece of equipment."""
    # TODO: replace with Gold/semantic layer call
    return {"equipment_id": equipment_id}


TOOLS = {t.name: t for t in (get_kpi, get_equipment_detail)}  # approved allowlist

# ---------- Prompts ----------

PLANNER_PROMPT = (
    "You plan read-only analytics for an industrial predictive-maintenance assistant. "
    "Return a plan of at most 4 steps using ONLY these tools: get_kpi(kpi_name, start_date, "
    "end_date, site_id?, equipment_type?), get_equipment_detail(equipment_id). "
    f"Valid kpi_name values: {', '.join(sorted(VALID_KPIS))}. Dates are YYYY-MM-DD. "
    "Do not execute anything and do not invent data or arguments."
)
JUDGE_PROMPT = (
    "You review a plan for a read-only maintenance assistant. Approve only if the plan answers "
    "the question, uses the right tools, stays read-only and invents no data. Otherwise "
    "return 'revise' with feedback_for_planner, or 'reject' if the question cannot be answered."
)
EXECUTOR_PROMPT = (
    "Answer the question using ONLY the tool results provided. Never invent numbers. "
    "Fill answer, evidence, filters, source_measure and limitations. "
    "Mention missing or failed data in limitations."
)

# ---------- Steps ----------


def _llm() -> AzureChatOpenAI:
    return AzureChatOpenAI(
        azure_endpoint=os.environ["LLM_ENDPOINT"],
        api_key=os.environ["LLM_API_KEY"],
        azure_deployment=os.environ["LLM_MODEL"],
        api_version=os.environ["LLM_API_VERSION"],
    )


def _ask(schema, system: str, human: str):
    llm = _llm().with_structured_output(schema, method="function_calling")
    return llm.invoke([SystemMessage(system), HumanMessage(human)])


def plan(question: str, feedback: str | None = None) -> Plan:
    human = question if not feedback else f"{question}\n\nReviewer feedback to address: {feedback}"
    return _ask(Plan, PLANNER_PROMPT, human)


def check_plan(p: Plan, allowed_sites: list[str]) -> list[str]:
    """Deterministic validation; returns a list of violations (empty = ok)."""
    errors = []
    for s in p.steps:
        if s.tool not in TOOLS:
            errors.append(f"Tool '{s.tool}' is not allowed.")
            continue
        site = s.args.get("site_id")
        if site and site not in allowed_sites:
            errors.append(f"site_id '{site}' is not permitted for this caller.")
        if s.tool == "get_kpi":
            if s.args.get("kpi_name") not in VALID_KPIS:
                errors.append(f"Invalid kpi_name '{s.args.get('kpi_name')}'.")
            try:
                if date.fromisoformat(s.args["end_date"]) < date.fromisoformat(s.args["start_date"]):
                    errors.append("end_date is before start_date.")
            except (KeyError, ValueError):
                errors.append("start_date and end_date must be YYYY-MM-DD.")
    return errors


def judge_plan(question: str, p: Plan) -> PlanReview:
    return _ask(PlanReview, JUDGE_PROMPT, f"Question: {question}\n\nPlan: {p.model_dump_json()}")


def execute(question: str, p: Plan) -> AssistantAnswer:
    limitations, messages = [], [SystemMessage(EXECUTOR_PROMPT), HumanMessage(question)]
    for i, s in enumerate(p.steps):
        try:
            result = TOOLS[s.tool].invoke(s.args)
        except Exception as e:  # tool errors are reported, not raised
            result = None
            limitations.append(f"Tool '{s.tool}' failed: {e}")
        messages.append(ToolMessage(json.dumps(result, default=str), tool_call_id=f"step{i}"))
    llm = _llm().with_structured_output(AssistantAnswer, method="function_calling")
    answer = llm.invoke(messages)
    answer.limitations += limitations
    return answer


def _refuse(reason: str) -> AssistantAnswer:
    return AssistantAnswer(answer="", evidence=[], filters={}, source_measure="", limitations=[reason])


# ---------- Graph ----------

MAX_REVISIONS = 2


class State(TypedDict, total=False):
    question: str
    allowed_sites: list[str]
    plan: Plan
    feedback: str | None
    revisions: int
    verdict: str  # approve | revise | reject
    reasons: list[str]
    answer: AssistantAnswer


def planner_node(s: State) -> State:
    return {"plan": plan(s["question"], s.get("feedback"))}


def evaluator_node(s: State) -> State:
    errors = check_plan(s["plan"], s["allowed_sites"])
    if errors:  # deterministic violation: revise without calling the LLM
        review = PlanReview(verdict="revise", reasons=errors, feedback_for_planner=" ".join(errors))
    else:
        review = judge_plan(s["question"], s["plan"])
    revisions = s.get("revisions", 0) + (review.verdict == "revise")
    return {
        "verdict": review.verdict,
        "reasons": review.reasons,
        "feedback": review.feedback_for_planner,
        "revisions": revisions,
    }


def route_after_evaluator(s: State) -> str:
    if s["verdict"] == "revise" and s["revisions"] <= MAX_REVISIONS:
        return "planner"
    return "executor"  # approved, rejected, or revisions exhausted


def executor_node(s: State) -> State:
    if s["verdict"] != "approve":  # nothing is executed
        return {"answer": _refuse(f"Plan not approved ({s['verdict']}): {' '.join(s['reasons'])}")}
    return {"answer": execute(s["question"], s["plan"])}


_graph = StateGraph(State)
_graph.add_node("planner", planner_node)
_graph.add_node("evaluator", evaluator_node)
_graph.add_node("executor", executor_node)
_graph.add_edge(START, "planner")
_graph.add_edge("planner", "evaluator")
_graph.add_conditional_edges("evaluator", route_after_evaluator, ["planner", "executor"])
_graph.add_edge("executor", END)
app = _graph.compile()


def run(question: str, allowed_sites: list[str]) -> AssistantAnswer:
    return app.invoke({"question": question, "allowed_sites": allowed_sites})["answer"]

run("What is the asset availability for the last 30 days?", ["site1", "site2"])