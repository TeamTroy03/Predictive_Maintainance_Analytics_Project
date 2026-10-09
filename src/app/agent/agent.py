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
import uuid
from datetime import date
from typing import Literal, TypedDict

from dotenv import load_dotenv
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import tool
from langchain_openai import AzureChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

load_dotenv()


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
    f"Valid kpi_name values: {', '.join(sorted(VALID_KPIS))}. Dates are YYYY-MM-DD. "
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

llm = AzureChatOpenAI(
    azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
    api_key=os.environ["AZURE_OPENAI_API_KEY"],
    azure_deployment=os.environ["AZURE_OPENAI_DEPLOYMENT_NAME"],
    api_version=os.environ["OPENAI_API_VERSION"],
    
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


def check_plan(p: Plan, allowed_sites: list[str]) -> list[str]:
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
    calls = [{"name": s.tool, "args": s.args, "id": f"step{i}"} for i, s in enumerate(p.steps)]
    messages.append(AIMessage(content="", tool_calls=calls))  # ToolMessages must answer tool_calls
    for call in calls:
        try:
            result = TOOLS[call["name"]].invoke(call["args"])
        except Exception as e:  # tool errors are reported, not raised
            result = None
            limitations.append(f"Tool '{call['name']}' failed: {e}")
        messages.append(ToolMessage(json.dumps(result, default=str), tool_call_id=call["id"]))
    answer = llm.with_structured_output(AssistantAnswer, method="function_calling").invoke(messages)
    answer.source_measure = ", ".join(c["name"] for c in calls)  # traceable, not LLM-written
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
    verdict: str  # approve | revise | reject | declined
    reasons: list[str]
    plan_text: str  # full plan + business summary shown to the user
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
        return {"answer": _refuse(f"Plan not approved ({s['verdict']}): {' '.join(s['reasons'])}")}
    return {"answer": execute(s["question"], s["plan"])}


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


def run(question: str, allowed_sites: list[str], approve=ask_on_console) -> AssistantAnswer:
    config = {"configurable": {"thread_id": uuid.uuid4().hex}}
    result = app.invoke({"question": question, "allowed_sites": allowed_sites}, config)
    if "__interrupt__" in result:  # waiting for the user's decision
        result = app.invoke(Command(resume=approve(result["__interrupt__"][0].value)), config)
    return result["answer"]


if __name__ == "__main__":
    print(run(
        "What is the current status of equipment EQ-001?",
        ["site1", "site2"],
    ))