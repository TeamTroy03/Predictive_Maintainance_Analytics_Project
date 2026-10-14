"""Payloads for the frontend pages, built from the governed KPIs (run_kpis) and converted for display:
ratios become percent and the alarm rate goes from per asset-day to per hour. The KPI table is monthly,
so KPIs cover the whole period in Gold and trends are monthly points."""

from collections.abc import Callable

from pydantic import BaseModel

from app.api.assets import asset_cte
from app.api.db import GoldDB
from app.api.kpis import REPEAT_FAILURES_SQL, UNPLANNED_FAILURES_SQL, Kpi
from app.api.schemas import (
    CostByType,
    FleetKpis,
    FleetSummary,
    GroupBy,
    MaintenanceKpis,
    MaintenanceSummary,
    MonthlyPoint,
    RepeatFailureAsset,
    RepeatFailureAssets,
    StatusCounts,
    TrendPoint,
)
from app.api.shaping import kpi_rows, scale, utc
from app.core.cache import TTLCache

CACHE_SECONDS = 60  # Gold only changes when the pipeline loads it; this spares the warehouse repeat queries
TOP_ASSETS = 10
MIN_FAILURES = 3  # an asset needs this many unplanned failures to be ranked: a 1-of-1 asset is not "100% repeat"


def cached[M: BaseModel](cache: TTLCache, key: str, model: type[M], build: Callable[[], M]) -> M:
    hit = cache.get(key)
    if hit:
        return model.model_validate_json(hit)
    value = build()
    cache.set(key, value.model_dump_json())
    return value


def _period(month_rows: list[dict]) -> tuple[str | None, str | None]:
    months = [r["month"] for r in month_rows]  # run_kpis returns them in order
    return (months[0], months[-1]) if months else (None, None)


def _status_counts(db: GoldDB) -> StatusCounts:
    """Same status definition as the Equipment list (assets.asset_cte), counted in SQL."""
    rows = db.query(
        asset_cte(db) + " SELECT asset_status AS status, COUNT(*) AS n FROM assets GROUP BY asset_status"
    )
    return StatusCounts(**{r["status"]: int(r["n"]) for r in rows})


def fleet_summary(db: GoldDB) -> FleetSummary:
    totals = kpi_rows(
        db,
        [Kpi.availability, Kpi.unplanned_downtime_hours, Kpi.mtbf_hours, Kpi.mttr_hours,
         Kpi.repeat_failure_rate, Kpi.overdue_work_orders, Kpi.maintenance_cost_eur, Kpi.alarm_rate],
    )
    months = kpi_rows(db, [Kpi.availability, Kpi.unplanned_downtime_hours], [GroupBy.month])
    row = totals.rows[0] if totals.rows else {}
    period_from, period_to = _period(months.rows)
    return FleetSummary(
        as_of=utc(totals.as_of_ts),
        period_from=period_from,
        period_to=period_to,
        kpis=FleetKpis(
            availability_pct=scale(row.get("availability"), 100),
            unplanned_downtime_h=scale(row.get("unplanned_downtime_hours")),
            mtbf_h=scale(row.get("mtbf_hours")),
            mttr_h=scale(row.get("mttr_hours")),
            repeat_failure_pct=scale(row.get("repeat_failure_rate"), 100),
            overdue_work_orders=scale(row.get("overdue_work_orders")),
            maintenance_cost_eur=scale(row.get("maintenance_cost_eur")),
            alarm_rate_per_h=scale(row.get("alarm_rate"), 1 / 24, digits=8),  # tiny: ~5e-6 on the current data
        ),
        status_counts=_status_counts(db),
        availability_trend=[
            TrendPoint(
                date=f"{r['month']}-01",
                availability_pct=scale(r["availability"], 100),
                unplanned_downtime_h=scale(r["unplanned_downtime_hours"]),
            )
            for r in months.rows
        ],
    )


def repeat_failure_assets(db: GoldDB, limit: int = TOP_ASSETS) -> RepeatFailureAssets:
    """Assets driving the Repeat-failure rate KPI, same pieces and same scope (kpi_equipment_month, whole period,
    Unknown equipment out), summed per asset. Status comes from asset_cte, so offline assets are included.
    LIMIT comes after the ORDER BY; the id tiebreak makes the order deterministic."""
    sql = (
        asset_cte(db)
        + f""", failures AS (
        SELECT k.equipment_key, {REPEAT_FAILURES_SQL} AS repeat_failures, {UNPLANNED_FAILURES_SQL} AS total_failures
        FROM {db.table("kpi_equipment_month")} k
        WHERE k.equipment_key <> -1
        GROUP BY k.equipment_key
    ), last_failure AS (
        SELECT equipment_key, MAX(source_start_ts) AS last_failure
        FROM {db.table("fact_downtime")}
        WHERE downtime_type = 'unplanned'
        GROUP BY equipment_key
    )
    SELECT a.equipment_id, a.equipment_name, a.production_line, a.asset_status,
           f.repeat_failures, f.total_failures, l.last_failure
    FROM failures f
    JOIN assets a ON a.equipment_key = f.equipment_key
    LEFT JOIN last_failure l ON l.equipment_key = f.equipment_key
    WHERE f.repeat_failures > 0 AND f.total_failures >= {MIN_FAILURES}
    ORDER BY f.repeat_failures DESC, f.total_failures DESC, a.equipment_id ASC
    LIMIT {int(limit)}"""
    )
    rows = db.query(sql)
    # The KPI's own numerator and denominator over all assets (not only the ranked ones), for the reconciliation
    fleet = db.query(
        f"""SELECT {REPEAT_FAILURES_SQL} AS repeat_failures, {UNPLANNED_FAILURES_SQL} AS total_failures
        FROM {db.table("kpi_equipment_month")} k WHERE k.equipment_key <> -1"""
    )[0]
    return RepeatFailureAssets(
        items=[
            RepeatFailureAsset(
                id=r["equipment_id"],
                name=r["equipment_name"],
                line=r["production_line"],
                status=r["asset_status"],
                repeat_failures=int(r["repeat_failures"]),
                total_failures=int(r["total_failures"]),
                repeat_rate_pct=round(int(r["repeat_failures"]) / int(r["total_failures"]) * 100, 1),
                last_failure=utc(r["last_failure"]),
            )
            for r in rows
        ],
        min_failures=MIN_FAILURES,
        fleet_repeat_failures=int(fleet["repeat_failures"] or 0),
        fleet_total_failures=int(fleet["total_failures"] or 0),
    )


def _cost_by_type(db: GoldDB) -> list[dict]:
    """Cost and work-order counts per maintenance type. Same scope as the KPI table (Unknown equipment out)."""
    return db.query(
        f"""
        SELECT t.maintenance_type AS type, t.is_planned AS is_planned,
               COALESCE(SUM(w.maintenance_cost_eur), 0) AS cost_eur,
               SUM(CASE WHEN w.is_cancelled THEN 0 ELSE 1 END) AS work_orders
        FROM {db.table("fact_maintenance_work_order")} w
        JOIN {db.table("dim_maintenance_type")} t ON w.maintenance_type_key = t.maintenance_type_key
        WHERE w.equipment_key <> -1
        GROUP BY t.maintenance_type, t.is_planned
        ORDER BY t.maintenance_type
        """
    )


def maintenance_summary(db: GoldDB) -> MaintenanceSummary:
    totals = kpi_rows(
        db,
        [Kpi.mtbf_hours, Kpi.mttr_hours, Kpi.overdue_work_orders, Kpi.maintenance_cost_eur,
         Kpi.repeat_failure_rate],
    )
    months = kpi_rows(db, [Kpi.mtbf_hours, Kpi.mttr_hours, Kpi.maintenance_cost_eur], [GroupBy.month])
    types = _cost_by_type(db)
    row = totals.rows[0] if totals.rows else {}
    period_from, period_to = _period(months.rows)

    known = [t for t in types if t["is_planned"] is not None]  # the Unknown type is neither planned nor reactive
    orders = sum(int(t["work_orders"]) for t in known)
    planned = sum(int(t["work_orders"]) for t in known if t["is_planned"])
    return MaintenanceSummary(
        as_of=utc(totals.as_of_ts),
        period_from=period_from,
        period_to=period_to,
        kpis=MaintenanceKpis(
            mtbf_h=scale(row.get("mtbf_hours")),
            mttr_h=scale(row.get("mttr_hours")),
            overdue_work_orders=scale(row.get("overdue_work_orders")),
            maintenance_cost_eur=scale(row.get("maintenance_cost_eur")),
            repeat_failure_pct=scale(row.get("repeat_failure_rate"), 100),
            planned_pct=scale(planned / orders, 100) if orders else None,
        ),
        monthly=[
            MonthlyPoint(
                month=r["month"],
                mtbf_h=scale(r["mtbf_hours"]),
                mttr_h=scale(r["mttr_hours"]),
                cost_eur=scale(r["maintenance_cost_eur"]),
            )
            for r in months.rows
        ],
        cost_by_type=[CostByType(type=t["type"], cost_eur=float(t["cost_eur"])) for t in types],
    )