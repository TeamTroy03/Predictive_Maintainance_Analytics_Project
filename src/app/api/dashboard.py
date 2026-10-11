"""Payloads for the frontend pages, built from the governed KPIs (run_kpis) and converted for display:
ratios become percent and the alarm rate goes from per asset-day to per hour. The KPI table is monthly,
so KPIs cover the whole period in Gold and trends are monthly points."""

import time
from collections.abc import Callable
from typing import Any

from app.api.assets import STATUS_PARAMS, asset_cte
from app.api.db import GoldDB
from app.api.kpis import Kpi
from app.api.schemas import (
    CostByType,
    FleetKpis,
    FleetSummary,
    GroupBy,
    MaintenanceKpis,
    MaintenanceSummary,
    MonthlyPoint,
    StatusCounts,
    TrendPoint,
)
from app.api.shaping import kpi_rows, scale, utc

CACHE_SECONDS = 60  # Gold only changes when the pipeline loads it; this spares the warehouse repeat queries


def cached(cache: dict, key: str, build: Callable[[], Any]) -> Any:
    hit = cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    value = build()
    cache[key] = (time.monotonic(), value)
    return value


def _period(month_rows: list[dict]) -> tuple[str | None, str | None]:
    months = [r["month"] for r in month_rows]  # run_kpis returns them in order
    return (months[0], months[-1]) if months else (None, None)


def _status_counts(db: GoldDB) -> StatusCounts:
    """Same status definition as the Equipment list (assets.asset_cte), counted in SQL."""
    rows = db.query(
        asset_cte(db) + " SELECT asset_status AS status, COUNT(*) AS n FROM assets GROUP BY asset_status",
        STATUS_PARAMS,
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