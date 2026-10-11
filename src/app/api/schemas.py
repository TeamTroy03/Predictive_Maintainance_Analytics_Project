"""Pydantic request and response models (validation + OpenAPI documentation)."""

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from app.api.kpis import Kpi


def _upper(value: Any) -> Any:
    return value.strip().upper() if isinstance(value, str) else value


SiteCode = Annotated[str, BeforeValidator(_upper), StringConstraints(pattern=r"^[A-Z0-9-]{1,20}$")]
EquipmentId = Annotated[str, BeforeValidator(_upper), StringConstraints(pattern=r"^EQ-\d{5}$")]
Month = Annotated[str, StringConstraints(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")]


# ---------------------------------------------------------------- analytics
class GroupBy(StrEnum):
    site = "site"
    equipment_type = "equipment_type"
    equipment = "equipment"
    month = "month"


class AnalyticsFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site_codes: list[SiteCode] | None = Field(None, max_length=20, description="e.g. ['PLT-B']")
    equipment_ids: list[EquipmentId] | None = Field(None, max_length=50, description="e.g. ['EQ-10002']")
    month_from: Month | None = Field(None, description="Inclusive, yyyy-MM")
    month_to: Month | None = Field(None, description="Inclusive, yyyy-MM")


class AnalyticsQuery(BaseModel):
    model_config = ConfigDict(
        extra="forbid",  # unknown fields (e.g. "sql") are rejected
        json_schema_extra={
            "examples": [
                {
                    "kpis": ["availability", "mtbf_hours", "mttr_hours"],
                    "group_by": ["site", "month"],
                    "filters": {"month_from": "2026-01", "month_to": "2026-06"},
                }
            ]
        },
    )
    kpis: list[Kpi] = Field(min_length=1, max_length=12, description="KPI ids from GET /kpis")
    group_by: list[GroupBy] = Field(
        default_factory=list, max_length=2, description="Empty = one fleet-level row"
    )
    filters: AnalyticsFilters = Field(default_factory=AnalyticsFilters)
    limit: int = Field(200, ge=1, le=1000)


class AnalyticsResult(BaseModel):
    kpis: dict[str, str] = Field(description="KPI id -> unit")
    rows: list[dict[str, Any]]
    row_count: int
    as_of_ts: datetime | None
    data_refreshed_ts: datetime | None
    source: str = "gold.kpi_equipment_month"


# ---------------------------------------------------------------- maintenance
class EquipmentHealth(BaseModel):
    equipment: dict[str, Any]
    kpis: dict[str, float | None] = Field(description="All KPIs over the equipment's history")
    recent_alarms: list[dict[str, Any]]
    open_work_orders: list[dict[str, Any]]
    as_of_ts: datetime | None


class WorkOrder(BaseModel):
    work_order_id: str
    equipment_id: str | None
    site_code: str | None
    priority: str | None
    status: str | None
    failure_mode: str | None
    created_ts: datetime | None
    due_ts: datetime | None


class WorkOrderPage(BaseModel):
    items: list[WorkOrder]
    page: int
    page_size: int
    total: int
    has_next: bool


Priority = Literal["P1", "P2", "P3", "P4"]


# ---------------------------------------------------------------- operations / assistant
class Health(BaseModel):
    status: Literal["ok"] = "ok"
    environment: str


class Readiness(BaseModel):
    status: Literal["ready", "not_ready"]
    gold: bool
    auth_configured: bool


class AssistantQuery(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "examples": [{"question": "Which machines at Valmora had the worst availability last month?"}]
        },
    )
    question: str = Field(min_length=1, max_length=1000)


class ToolEvidence(BaseModel):
    tool: str
    arguments: dict[str, Any]
    status: Literal["ok", "error"]
    result: Any = Field(description="What the tool returned (from Gold)")


class AssistantAnswer(BaseModel):
    answer: str
    evidence: list[ToolEvidence] = Field(description="Every tool call, its arguments and result")
    grounded: bool = Field(description="True if at least one tool returned data")
    limitations: list[str]
