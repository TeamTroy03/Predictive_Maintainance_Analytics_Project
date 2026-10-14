"""Structured assistant output: answer, evidence, filters, source_or_measure, limitations (spec 6.5).
Also the plan the LLM proposes, the reviewer's verdict and the state passed between the graph nodes."""

from typing import Literal, TypedDict

from pydantic import BaseModel, Field


class PlanStep(BaseModel):
    tool: str
    args: dict
    purpose: str


MAX_PLAN_STEPS = 7  # one step is one tool call; the planner prompt quotes this number too


class Plan(BaseModel):
    approach: str
    steps: list[PlanStep] = Field(max_length=MAX_PLAN_STEPS)


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


class State(TypedDict, total=False):
    question: str
    run_id: str  # correlation ID of this question, on every log line
    conversation_id: str | None  # memory key: questions with the same ID share their history
    history: str  # earlier turns of the conversation as prompt text ("" for the first question)
    plan: Plan
    feedback: str | None
    revisions: int
    verdict: str  # approve | revise | reject | declined
    reasons: list[str]
    plan_text: str  # plain-language plan shown to the user (tool steps stay in `plan`)
    answer: AssistantAnswer
    trace: list[dict]  # one {tool, args, ok, result} per executed step, for the API's evidence