"""All REST endpoints. Resources are nouns, verbs follow REST:
GET reads a resource, POST runs a query that needs a JSON body."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import JSONResponse

from app.api.agent import AgentTools, answer_question
from app.api.config import Settings, get_settings
from app.api.db import GoldDB
from app.api.errors import ERROR_RESPONSES, ErrorResponse
from app.api.kpis import KPI_DEFINITIONS
from app.api.schemas import (
    AnalyticsQuery,
    AnalyticsResult,
    AssistantAnswer,
    AssistantQuery,
    EquipmentHealth,
    Health,
    Priority,
    Readiness,
    WorkOrderPage,
)
from app.api.security import require_auth
from app.api.services import equipment_health, overdue_work_orders, run_kpis


def get_db(request: Request) -> GoldDB:
    return request.app.state.db


public = APIRouter(tags=["operations"])
api = APIRouter(dependencies=[Depends(require_auth)], responses=ERROR_RESPONSES)


# ---------------------------------------------------------------- operations (no auth)
@public.get("/health", response_model=Health, summary="Liveness: the service is running")
def health(settings: Settings = Depends(get_settings)) -> Health:
    return Health(environment=settings.environment)


@public.get(
    "/ready",
    response_model=Readiness,
    summary="Readiness: Gold reachable, auth set",
    responses={503: {"model": Readiness}},
)
def ready(request: Request, settings: Settings = Depends(get_settings)) -> JSONResponse:
    body = Readiness(
        status="not_ready",
        gold=request.app.state.db.is_ready(),
        auth_configured=bool(settings.api_key_list()) or settings.jwt_enabled,
    )
    if body.gold and body.auth_configured:
        body.status = "ready"
    return JSONResponse(body.model_dump(), status_code=200 if body.status == "ready" else 503)


# ---------------------------------------------------------------- analytics
@api.get("/kpis", tags=["analytics"], summary="List the governed KPIs")
def list_kpis() -> list[dict[str, str]]:
    return [
        {"id": k.value, "unit": unit, "description": desc} for k, (unit, _, desc) in KPI_DEFINITIONS.items()
    ]


@api.post(
    "/analytics/query",
    response_model=AnalyticsResult,
    tags=["analytics"],
    summary="Compute KPIs with optional grouping and filters",
)
def analytics_query(query: AnalyticsQuery, db: GoldDB = Depends(get_db)) -> AnalyticsResult:
    return run_kpis(db, query)


# ---------------------------------------------------------------- domain endpoints
@api.get(
    "/equipment/{equipment_id}/health",
    response_model=EquipmentHealth,
    tags=["maintenance"],
    summary="Health summary of one asset",
    responses={404: {"model": ErrorResponse, "description": "Unknown equipment"}},
)
def get_equipment_health(
    equipment_id: Annotated[str, Path(pattern=r"^[Ee][Qq]-\d{5}$", examples=["EQ-10002"])],
    db: GoldDB = Depends(get_db),
) -> EquipmentHealth:
    return equipment_health(db, equipment_id.upper())


@api.get(
    "/work-orders/overdue",
    response_model=WorkOrderPage,
    tags=["maintenance"],
    summary="Overdue work orders, most urgent first (paginated)",
)
def get_overdue_work_orders(
    site_code: Annotated[str | None, Query(pattern=r"^[A-Za-z0-9-]{1,20}$")] = None,
    priority: Priority | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    db: GoldDB = Depends(get_db),
) -> WorkOrderPage:
    return overdue_work_orders(db, site_code, priority, page, page_size)


# ---------------------------------------------------------------- assistant
@api.post(
    "/assistant/query",
    response_model=AssistantAnswer,
    tags=["assistant"],
    summary="Ask a question; the agent may only use the approved data tools",
    responses={429: {"model": ErrorResponse, "description": "LLM rate limit"}},
)
def assistant_query(body: AssistantQuery, db: GoldDB = Depends(get_db)) -> AssistantAnswer:
    return answer_question(body.question, AgentTools(db))
