"""Connection point for the AI agent (built by the team, spec 6.5).

The API already does everything around the agent: the endpoint POST /assistant/query,
authentication, validation of the question, errors, logs and correlation IDs.
The agent only has to implement `answer_question` below.

`tools` gives the agent the ONLY data functions it may use. They are the same functions
as the REST endpoints, so the agent can never compute a number differently from the API:

    tools.get_kpis(AnalyticsQuery(...))                  -> AnalyticsResult
    tools.get_equipment_health("EQ-10002")               -> EquipmentHealth
    tools.list_overdue_work_orders(site_code, priority)  -> WorkOrderPage

With LangChain Core, wrap each one with @tool (they have typed arguments), bind them to
the chat model, execute the tool calls and record each call in `evidence`.
For errors raise ApiError(429, "rate_limited", ...) or ApiError(503, "assistant_unavailable", ...).
"""

from app.db import GoldDB
from app.errors import ApiError
from app.schemas import (
    AnalyticsQuery,
    AnalyticsResult,
    AssistantAnswer,
    EquipmentHealth,
    Priority,
    WorkOrderPage,
)
from app.services import equipment_health, overdue_work_orders, run_kpis


class AgentTools:
    """The approved, read-only data functions available to the agent."""

    def __init__(self, db: GoldDB) -> None:
        self._db = db

    def get_kpis(self, query: AnalyticsQuery) -> AnalyticsResult:
        """KPIs (availability, MTBF, MTTR, alarms, cost...) with grouping and filters."""
        return run_kpis(self._db, query)

    def get_equipment_health(self, equipment_id: str) -> EquipmentHealth:
        """KPIs, latest sensor alarms and open work orders of one asset."""
        return equipment_health(self._db, equipment_id.upper())

    def list_overdue_work_orders(
        self, site_code: str | None = None, priority: Priority | None = None
    ) -> WorkOrderPage:
        """Overdue work orders, most urgent first."""
        return overdue_work_orders(self._db, site_code, priority, page=1, page_size=20)


def answer_question(question: str, tools: AgentTools) -> AssistantAnswer:
    """TODO (agent team): call the LLM, let it use `tools`, return the answer.

    Return AssistantAnswer(answer=..., evidence=[ToolEvidence(...), ...],
                           grounded=..., limitations=[...]).
    """
    raise ApiError(503, "assistant_unavailable", "The agent is not connected yet")
