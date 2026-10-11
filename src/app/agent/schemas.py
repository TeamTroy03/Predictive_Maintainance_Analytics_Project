"""Structured assistant output: answer, evidence, filters, source_or_measure, limitations (spec 6.5).
Also the plan the LLM proposes, the reviewer's verdict and the state passed between the graph nodes."""

from typing import Literal, TypedDict

from pydantic import BaseModel, Field


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