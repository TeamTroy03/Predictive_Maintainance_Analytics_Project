"""Pydantic request and response models (validation + OpenAPI documentation)."""

from datetime import date, datetime
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


# ---------------------------------------------------------------- auth
class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256, repr=False)  # bounded: scrypt cost grows with length


class UserOut(BaseModel):
    email: str
    name: str
    role: str = Field(description="Shown in the profile menu; every website user is a reader")


# ---------------------------------------------------------------- dashboard (what the frontend pages read)
# KPI values are the governed ones, converted for display: ratios as percent, alarm rate per hour.
class FleetKpis(BaseModel):
    availability_pct: float | None
    unplanned_downtime_h: float | None
    mtbf_h: float | None
    mttr_h: float | None
    repeat_failure_pct: float | None
    overdue_work_orders: int | None
    maintenance_cost_eur: float | None
    alarm_rate_per_h: float | None = Field(description="alarm_rate (per asset-day) divided by 24")


class StatusCounts(BaseModel):
    operational: int = 0
    warning: int = 0
    critical: int = 0
    offline: int = 0


class TrendPoint(BaseModel):
    date: str = Field(description="First day of the month: the KPI table is monthly")
    availability_pct: float | None
    unplanned_downtime_h: float | None


class FleetSummary(BaseModel):
    as_of: datetime | None = Field(description="Data as of (Gold AS_OF timestamp)")
    period_from: str | None = Field(description="First month in Gold, yyyy-MM; KPIs cover the whole period")
    period_to: str | None
    kpis: FleetKpis
    status_counts: StatusCounts = Field(description="Assets by active alarm (critical/warning, as in Critical Exceptions); offline = standby, decommissioned or no data")
    availability_trend: list[TrendPoint]


class RepeatFailureAsset(BaseModel):
    id: str
    name: str | None
    line: str | None
    status: AssetStatus
    repeat_failures: int
    total_failures: int = Field(description="Unplanned failures of the asset: the KPI's denominator")
    repeat_rate_pct: float
    last_failure: datetime | None = Field(description="Start of the asset's latest unplanned downtime interval")


class RepeatFailureAssets(BaseModel):
    items: list[RepeatFailureAsset]
    min_failures: int = Field(description="Assets with fewer unplanned failures are not ranked")
    fleet_repeat_failures: int = Field(description="Sum over ALL assets = numerator of the Repeat-failure rate KPI")
    fleet_total_failures: int = Field(description="Sum over ALL assets = denominator of the Repeat-failure rate KPI")


class MaintenanceKpis(BaseModel):
    mtbf_h: float | None
    mttr_h: float | None
    overdue_work_orders: int | None
    maintenance_cost_eur: float | None
    repeat_failure_pct: float | None
    planned_pct: float | None = Field(description="Share of non-cancelled work orders that are planned (not Corrective)")


class MonthlyPoint(BaseModel):
    month: str = Field(description="yyyy-MM")
    mtbf_h: float | None
    mttr_h: float | None
    cost_eur: float | None


class CostByType(BaseModel):
    type: str
    cost_eur: float


class MaintenanceSummary(BaseModel):
    as_of: datetime | None
    period_from: str | None
    period_to: str | None
    kpis: MaintenanceKpis
    monthly: list[MonthlyPoint]
    cost_by_type: list[CostByType]


# ---------------------------------------------------------------- equipment, sensors, alarms, work orders
# Fields Gold does not have (health score, remaining useful life, thresholds, technician) are simply absent.
AssetStatus = Literal["operational", "warning", "critical", "offline"]
SensorRange = Literal["1h", "24h", "7d", "30d", "all"]
WorkOrderStatus = Literal["open", "overdue", "completed", "cancelled"]


class AssetRow(BaseModel):
    id: str
    name: str | None
    type: str | None
    line: str | None
    site: str | None
    status: AssetStatus = Field(description="critical/warning = has an active critical/warning alarm (as in Critical Exceptions); decommissioned/standby = offline")
    availability_pct: float | None
    manufacturer: str | None
    criticality: str | None
    installed: date | None
    active_alarms: int = Field(description="Alarm readings in the whole period: Gold has no acknowledged/cleared state")
    last_maintenance: datetime | None = Field(description="Last closed work order")


class AssetPage(BaseModel):
    items: list[AssetRow]
    total: int
    page: int
    page_size: int


class AssetKpis(BaseModel):
    availability_pct: float | None
    mtbf_h: float | None
    mttr_h: float | None
    unplanned_downtime_h: float | None


class LatestReading(BaseModel):
    sensor: str
    value: float | None
    unit: str | None
    status: AssetStatus


class AssetDetail(AssetRow):
    kpis: AssetKpis
    latest_readings: list[LatestReading]
    components: list[dict[str, Any]] = Field(default_factory=list, description="Not in Gold: always empty")


class SensorPoint(BaseModel):
    t: datetime
    value: float | None
    anomaly: bool = Field(description="Gold's is_alarm flag of the reading")


class Sensor(BaseModel):
    id: str
    name: str
    unit: str | None
    warn: float | None = Field(None, description="Not in Gold")
    crit: float | None = Field(None, description="Not in Gold")
    lower_is_bad: bool = False
    points: list[SensorPoint]


class SensorSeries(BaseModel):
    asset_id: str
    range: SensorRange
    sensors: list[Sensor]


class Alarm(BaseModel):
    id: str
    asset_id: str | None
    severity: Literal["critical", "warning"]
    sensor: str | None
    message: str
    value: float | None
    threshold: float | None = Field(None, description="Not in Gold")
    unit: str | None
    time: datetime
    acknowledged: bool = Field(False, description="Gold keeps no acknowledgements: always false")
    state: Literal["active", "cleared"] = Field(
        "active", description="Derived: cleared once the sensor has a later normal reading"
    )


class WorkOrderItem(BaseModel):
    id: str
    asset_id: str | None
    type: str | None = Field(description="Maintenance type")
    status: WorkOrderStatus
    priority: str | None
    title: str = Field(description="Built from maintenance type and failure mode: Gold has no title")
    opened: datetime | None
    due: datetime | None
    completed: datetime | None
    downtime_h: float | None
    cost: float | None


class WorkOrderItemPage(BaseModel):
    items: list[WorkOrderItem]
    total: int = Field(description="Work orders matching the filter, over all pages")
    page: int
    page_size: int


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
    conversation_id: str | None = Field(
        None,
        pattern=r"^[A-Za-z0-9._-]{1,64}$",
        description="Questions sent with the same ID share a memory, so follow-ups ('and for pumps?') work",
    )


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
    filters: dict[str, Any] = Field(default_factory=dict, description="Filters the agent applied")
    source_measure: str = Field("", description="Tools (and SQL for detail queries) the answer comes from")


class AssistantApproval(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={"examples": [{"run_id": "b3f1c0de-0000-4000-8000-000000000000", "approved": True}]},
    )
    run_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,64}$", description="run_id returned by POST /assistant/query")
    approved: bool = Field(description="True runs the plan, False declines it")


class AssistantResponse(BaseModel):
    status: Literal["awaiting_approval", "answered"]
    run_id: str = Field(description="Also the correlation ID of the query request; send it to /assistant/approve")
    plan: str | None = Field(None, description="Plan waiting for approval (status awaiting_approval)")
    result: AssistantAnswer | None = Field(None, description="The answer (status answered)")
