"""All REST endpoints. Resources are nouns, verbs follow REST:
GET reads a resource, POST runs a query that needs a JSON body."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.responses import JSONResponse

from app.api.agent import approve_run, start_question
from app.api.config import Settings, get_settings
from app.api.assets import (
    asset_detail,
    list_alarms,
    list_assets,
    list_work_orders,
    sensor_series,
)
from app.api.dashboard import cached, fleet_summary, maintenance_summary
from app.api.db import GoldDB
from app.api.errors import ERROR_RESPONSES, ApiError, ErrorResponse
from app.api.kpis import KPI_DEFINITIONS
from app.api.schemas import (
    Alarm,
    AnalyticsQuery,
    AnalyticsResult,
    AssetDetail,
    AssetPage,
    AssetStatus,
    AssistantApproval,
    AssistantQuery,
    AssistantResponse,
    EquipmentHealth,
    FleetSummary,
    Health,
    LoginRequest,
    MaintenanceSummary,
    Priority,
    Readiness,
    SensorRange,
    SensorSeries,
    UserOut,
    WorkOrderItem,
    WorkOrderPage,
    WorkOrderStatus,
)
from app.api.security import (
    COOKIE_NAME,
    authenticate,
    is_throttled,
    issue_token,
    record_failure,
    require_auth,
)
from app.api.services import equipment_health, overdue_work_orders, run_kpis


def get_db(request: Request) -> GoldDB:
    return request.app.state.db


public = APIRouter(tags=["operations"])
auth = APIRouter(prefix="/auth", tags=["auth"], responses={401: ERROR_RESPONSES[401]})
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


# ---------------------------------------------------------------- auth (website session cookie)
def _user(email: str, settings: Settings) -> UserOut:
    return UserOut(email=email, name=email.split("@")[0], role=settings.jwt_required_role)


@auth.post(
    "/login",
    response_model=UserOut,
    summary="Sign in: sets the session JWT as an HttpOnly, Secure, SameSite=Strict cookie",
    responses={
        429: {"model": ErrorResponse, "description": "Too many failed attempts"},
        503: {"model": ErrorResponse, "description": "Login is not configured"},
    },
)
def login(body: LoginRequest, request: Request, response: Response, settings: Settings = Depends(get_settings)):
    if not settings.cookie_auth_enabled:
        raise ApiError(503, "login_unavailable", "Login is not configured")
    email = body.email.strip().lower()
    failures = request.app.state.login_failures
    key = f"{request.client.host if request.client else '-'}|{email}"
    if is_throttled(failures, key):
        raise ApiError(429, "too_many_attempts", "Too many failed sign-in attempts, try again later")
    if not authenticate(email, body.password, settings):
        record_failure(failures, key)
        raise ApiError(401, "unauthorized", "Invalid email or password")  # same for unknown user and wrong password
    failures.pop(key, None)
    response.set_cookie(
        COOKIE_NAME,
        issue_token(email, settings),
        max_age=settings.session_minutes * 60,
        httponly=True,  # JavaScript can never read the token
        secure=settings.cookie_secure,
        samesite="strict",
        path="/",
    )
    return _user(email, settings)


@auth.post("/logout", summary="Sign out: clears the session cookie")
def logout(response: Response, settings: Settings = Depends(get_settings)) -> dict[str, bool]:
    response.delete_cookie(
        COOKIE_NAME, path="/", httponly=True, secure=settings.cookie_secure, samesite="strict"
    )
    return {"ok": True}


@auth.get("/me", response_model=UserOut, summary="The signed-in user (401 if there is no valid session)")
def me(who: str = Depends(require_auth), settings: Settings = Depends(get_settings)) -> UserOut:
    if not who.startswith("user:"):  # an API key or service token is not a website user
        raise ApiError(401, "unauthorized", "Not signed in")
    return _user(who.removeprefix("user:"), settings)


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


# ---------------------------------------------------------------- dashboard (frontend pages)
@api.get(
    "/fleet/summary",
    response_model=FleetSummary,
    tags=["dashboard"],
    summary="Fleet health KPIs, status counts and monthly availability trend",
)
def get_fleet_summary(request: Request, db: GoldDB = Depends(get_db)) -> FleetSummary:
    return cached(request.app.state.dashboard_cache, "fleet", lambda: fleet_summary(db))


@api.get(
    "/maintenance/summary",
    response_model=MaintenanceSummary,
    tags=["dashboard"],
    summary="Maintenance KPIs, monthly MTBF/MTTR/cost and cost by maintenance type",
)
def get_maintenance_summary(request: Request, db: GoldDB = Depends(get_db)) -> MaintenanceSummary:
    return cached(request.app.state.dashboard_cache, "maintenance", lambda: maintenance_summary(db))


# ---------------------------------------------------------------- equipment, sensors, alarms, work orders
EquipmentIdParam = Annotated[str, Path(pattern=r"^[Ee][Qq]-\d{5}$", examples=["EQ-10002"])]


@api.get(
    "/assets",
    response_model=AssetPage,
    tags=["equipment"],
    summary="Equipment with status, most urgent first (paginated, searchable)",
)
def get_assets(
    request: Request,
    status: AssetStatus | None = None,
    q: Annotated[str | None, Query(max_length=64, description="Matches id, name, type or line")] = None,
    has_sensors: Annotated[bool, Query(description="Only assets that have sensor readings")] = False,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 50,
    db: GoldDB = Depends(get_db),
) -> AssetPage:
    def build() -> AssetPage:
        return list_assets(db, status, q, has_sensors, page, page_size)

    if q:  # free text is not cached: every distinct search would add an entry
        return build()
    return cached(request.app.state.dashboard_cache, f"assets|{status}|{has_sensors}|{page}|{page_size}", build)


@api.get(
    "/assets/{equipment_id}",
    response_model=AssetDetail,
    tags=["equipment"],
    summary="One asset: status, KPIs and latest sensor readings",
    responses={404: {"model": ErrorResponse, "description": "Unknown equipment"}},
)
def get_asset(equipment_id: EquipmentIdParam, db: GoldDB = Depends(get_db)) -> AssetDetail:
    return asset_detail(db, equipment_id.upper())


@api.get(
    "/assets/{equipment_id}/sensors",
    response_model=SensorSeries,
    tags=["equipment"],
    summary="Sensor readings of one asset; the window ends at its last reading",
    responses={404: {"model": ErrorResponse, "description": "Unknown equipment"}},
)
def get_sensor_series(
    equipment_id: EquipmentIdParam,
    range_: Annotated[SensorRange, Query(alias="range")] = "24h",
    db: GoldDB = Depends(get_db),
) -> SensorSeries:
    return sensor_series(db, equipment_id.upper(), range_)


@api.get("/alarms", response_model=list[Alarm], tags=["equipment"], summary="Alarm readings, newest first")
def get_alarms(
    asset_id: Annotated[str | None, Query(pattern=r"^[Ee][Qq]-\d{5}$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    db: GoldDB = Depends(get_db),
) -> list[Alarm]:
    return list_alarms(db, asset_id.upper() if asset_id else None, limit)


@api.get(
    "/maintenance/work-orders",
    response_model=list[WorkOrderItem],
    tags=["maintenance"],
    summary="Work orders: overdue first, then open, then the rest",
)
def get_work_orders(
    status: WorkOrderStatus | None = None,
    asset_id: Annotated[str | None, Query(pattern=r"^[Ee][Qq]-\d{5}$")] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    db: GoldDB = Depends(get_db),
) -> list[WorkOrderItem]:
    return list_work_orders(db, status, asset_id.upper() if asset_id else None, limit)


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
# The agent pauses for human approval of its plan, so the assistant has two steps. Both calls make
# several LLM calls: use a client timeout of about 300 s.
@api.post(
    "/assistant/query",
    response_model=AssistantResponse,
    tags=["assistant"],
    summary="Step 1: ask a question; returns the agent's plan waiting for approval",
    responses={429: {"model": ErrorResponse, "description": "LLM rate limit"}},
)
def assistant_query(body: AssistantQuery) -> AssistantResponse:
    return start_question(body.question)


@api.post(
    "/assistant/approve",
    response_model=AssistantResponse,
    tags=["assistant"],
    summary="Step 2: approve or decline the plan; returns the answer",
    responses={
        404: {"model": ErrorResponse, "description": "Unknown or expired run_id"},
        409: {"model": ErrorResponse, "description": "The run is not waiting for approval"},
        429: {"model": ErrorResponse, "description": "LLM rate limit"},
    },
)
def assistant_approve(body: AssistantApproval) -> AssistantResponse:
    return approve_run(body.run_id, body.approved)
