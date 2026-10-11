"""Connection point to the AI agent (`app.agent.agent`, spec 6.5).

The agent plans, reviews the plan, then pauses until a human approves it. The API therefore has
two steps: POST /assistant/query returns the plan (`awaiting_approval`) and POST /assistant/approve
sends the decision and returns the answer. The run state lives in the agent process (in memory),
so run a single worker.

The agent is imported lazily: importing it builds the Azure OpenAI client and needs the
AZURE_OPENAI_* variables, which must not be required just to start the API.
"""

import logging
import uuid
from types import ModuleType

from app.api.errors import ApiError
from app.api.observability import correlation_id
from app.api.schemas import AssistantAnswer, AssistantResponse, ToolEvidence

logger = logging.getLogger("api")


def _agent() -> ModuleType:
    try:
        from app.agent import agent
    except (KeyError, ImportError) as exc:  # missing AZURE_OPENAI_* variable or package
        logger.error("agent_unavailable", extra={"error": type(exc).__name__})
        raise ApiError(503, "assistant_unavailable", "The agent is not configured") from exc
    return agent


def _call(func, *args):
    """Run an agent function; LLM failures become clean API errors (no secrets or tracebacks)."""
    try:
        return func(*args)
    except ApiError:
        raise
    except Exception as exc:
        if type(exc).__name__ == "RateLimitError":  # openai.RateLimitError, still failing after retries
            raise ApiError(429, "rate_limited", "The language model is rate limited, retry later") from exc
        logger.error("agent_failed", extra={"error": type(exc).__name__})
        raise ApiError(503, "assistant_unavailable", "The agent could not answer right now") from exc


def _has_data(result: object) -> bool:
    return isinstance(result, dict) and "error" not in result and result.get("row_count", 1) != 0


def _answered(run_id: str, state: dict) -> AssistantResponse:
    """Map the agent's final state to the API shape."""
    agent_answer = state["answer"]
    evidence = [
        ToolEvidence(tool=t["tool"], arguments=t["args"], status="ok" if t["ok"] else "error", result=t["result"])
        for t in state.get("trace", [])
    ]
    result = AssistantAnswer(
        answer=agent_answer.answer,
        evidence=evidence,
        grounded=any(t["ok"] and _has_data(t["result"]) for t in state.get("trace", [])),
        limitations=agent_answer.limitations,
        filters=agent_answer.filters,
        source_measure=agent_answer.source_measure,
    )
    return AssistantResponse(status="answered", run_id=run_id, result=result)


def start_question(question: str) -> AssistantResponse:
    """Plan the question. Returns the plan awaiting approval, or the answer if no approval is needed
    (e.g. the plan was rejected: nothing runs and the reason is in limitations)."""
    agent = _agent()
    # The request's correlation ID becomes the run ID, so API logs and agent.log share one ID.
    # A client-supplied ID that already belongs to a run would resume that run: use a fresh one.
    run_id = correlation_id.get()
    if run_id == "-" or agent.run_exists(run_id):
        run_id = uuid.uuid4().hex
    run_id, plan, state = _call(agent.start_run, question, run_id)
    if plan is not None:
        return AssistantResponse(status="awaiting_approval", run_id=run_id, plan=plan)
    return _answered(run_id, state)


def approve_run(run_id: str, approved: bool) -> AssistantResponse:
    """Send the human decision for a pending plan; returns the final answer."""
    agent = _agent()
    if not agent.run_exists(run_id):
        raise ApiError(404, "unknown_run", "No assistant run with this run_id (it may have expired)")
    if agent.pending_plan(run_id) is None:
        raise ApiError(409, "run_not_pending", "This run is not waiting for approval")
    logger.info("assistant_decision", extra={"run_id": run_id, "approved": approved})
    return _answered(run_id, _call(agent.finish_run, run_id, approved))
