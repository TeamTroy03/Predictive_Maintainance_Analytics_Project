"""Business queries over the Gold star schema, shared by the REST routes and the assistant."""

from decimal import Decimal
from typing import Any

from app.api.db import GoldDB
from app.api.errors import ApiError
from app.api.kpis import KPI_DEFINITIONS, Kpi
from app.api.schemas import (
    AnalyticsQuery,
    AnalyticsResult,
    EquipmentHealth,
    GroupBy,
    WorkOrder,
    WorkOrderPage,
)

# group_by option -> columns it adds (SQL expression, output name)
GROUP_COLUMNS = {
    GroupBy.site: [("s.site_code", "site_code"), ("s.site_name", "site_name")],
    GroupBy.equipment_type: [("t.equipment_type", "equipment_type")],
    GroupBy.equipment: [("e.equipment_id", "equipment_id"), ("e.equipment_name", "equipment_name")],
    GroupBy.month: [("d.year_month", "month")],
}


def _number(value: Any, digits: int = 4) -> Any:
    return round(float(value), digits) if isinstance(value, Decimal | float) else value


def run_kpis(db: GoldDB, q: AnalyticsQuery, digits: int = 4) -> AnalyticsResult:
    """digits: rounding of the KPI values. The dashboard asks for more, because it converts units afterwards."""
    groups = [col for g in q.group_by for col in GROUP_COLUMNS[g]]
    select = [f"{expr} AS {name}" for expr, name in groups]
    select += [f"{KPI_DEFINITIONS[k][1]} AS {k.value}" for k in q.kpis]
    select += ["MAX(k.as_of_ts) AS as_of_ts", "MAX(k.gold_loaded_ts) AS loaded_ts"]

    where, params = [], {}
    f = q.filters
    for field, column in (("site_codes", "s.site_code"), ("equipment_ids", "e.equipment_id")):
        values = getattr(f, field) or []
        if values:
            names = [f"{field}_{i}" for i in range(len(values))]
            where.append(f"{column} IN ({', '.join(':' + n for n in names)})")
            params.update(zip(names, values, strict=True))
    if f.month_from:
        where.append("d.year_month >= :month_from")
        params["month_from"] = f.month_from
    if f.month_to:
        where.append("d.year_month <= :month_to")
        params["month_to"] = f.month_to

    sql = f"""
        SELECT {", ".join(select)}
        FROM {db.table("kpi_equipment_month")} k
        LEFT JOIN {db.table("dim_site")} s ON k.site_key = s.site_key
        LEFT JOIN {db.table("dim_equipment_type")} t ON k.equipment_type_key = t.equipment_type_key
        LEFT JOIN {db.table("dim_equipment")} e ON k.equipment_key = e.equipment_key
        LEFT JOIN {db.table("dim_date")} d ON k.month_start_date_key = d.date_key
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    if groups:
        group_sql = ", ".join(expr for expr, _ in groups)
        sql += f" GROUP BY {group_sql} ORDER BY {group_sql}"
    sql += f" LIMIT {q.limit}"

    raw = db.query(sql, params)
    # Without GROUP BY an aggregate always returns one row; no data shows as NULL timestamps.
    raw = [r for r in raw if r["loaded_ts"] is not None]
    rows = [{k: _number(v, digits) for k, v in r.items() if k not in ("as_of_ts", "loaded_ts")} for r in raw]
    return AnalyticsResult(
        kpis={k.value: KPI_DEFINITIONS[k][0] for k in q.kpis},
        rows=rows,
        row_count=len(rows),
        as_of_ts=max((r["as_of_ts"] for r in raw), default=None),
        data_refreshed_ts=max((r["loaded_ts"] for r in raw), default=None),
    )


def equipment_health(db: GoldDB, equipment_id: str) -> EquipmentHealth:
    found = db.query(
        f"""SELECT e.equipment_key, e.equipment_id, e.equipment_name, e.criticality, e.status,
                   s.site_code, s.site_name, t.equipment_type
            FROM {db.table("dim_equipment")} e
            LEFT JOIN {db.table("dim_site")} s ON e.site_key = s.site_key
            LEFT JOIN {db.table("dim_equipment_type")} t ON e.equipment_type_key = t.equipment_type_key
            WHERE e.equipment_id = :equipment_id""",
        {"equipment_id": equipment_id},
    )
    if not found:
        raise ApiError(404, "not_found", f"Equipment {equipment_id} not found")
    equipment = found[0]
    key = equipment.pop("equipment_key")

    kpis = run_kpis(db, AnalyticsQuery(kpis=list(Kpi), filters={"equipment_ids": [equipment_id]}))
    alarms = db.query(
        f"""SELECT event_ts, sensor_type, reading_value, canonical_unit, alarm_state, alarm_code
            FROM {db.table("fact_sensor_reading")}
            WHERE equipment_key = :key AND is_alarm = TRUE
            ORDER BY event_ts DESC LIMIT 10""",
        {"key": key},
    )
    work_orders = db.query(
        f"""SELECT work_order_id, priority, status, due_ts, is_overdue
            FROM {db.table("fact_maintenance_work_order")}
            WHERE equipment_key = :key AND is_open = TRUE
            ORDER BY is_overdue DESC, due_ts LIMIT 20""",
        {"key": key},
    )
    values = kpis.rows[0] if kpis.rows else {}
    return EquipmentHealth(
        equipment=equipment,
        kpis={k.value: values.get(k.value) for k in Kpi},
        recent_alarms=alarms,
        open_work_orders=work_orders,
        as_of_ts=kpis.as_of_ts,
    )


def overdue_work_orders(
    db: GoldDB, site_code: str | None, priority: str | None, page: int, page_size: int
) -> WorkOrderPage:
    where, params = ["w.is_overdue = TRUE"], {}
    if site_code:
        where.append("s.site_code = :site_code")
        params["site_code"] = site_code.upper()
    if priority:
        where.append("w.priority = :priority")
        params["priority"] = priority

    source = f"""
        FROM {db.table("fact_maintenance_work_order")} w
        LEFT JOIN {db.table("dim_site")} s ON w.site_key = s.site_key
        LEFT JOIN {db.table("dim_failure_mode")} f ON w.failure_mode_key = f.failure_mode_key
        WHERE {" AND ".join(where)}
    """
    total = db.query(f"SELECT COUNT(*) AS n {source}", params)[0]["n"]
    rows = db.query(
        f"""SELECT w.work_order_id, w.equipment_id, s.site_code, w.priority, w.status,
                   f.failure_mode_desc AS failure_mode, w.created_ts, w.due_ts
            {source}
            ORDER BY w.priority, w.due_ts, w.work_order_id
            LIMIT {page_size} OFFSET {(page - 1) * page_size}""",
        params,
    )
    return WorkOrderPage(
        items=[WorkOrder(**r) for r in rows],
        page=page,
        page_size=page_size,
        total=total,
        has_next=page * page_size < total,
    )
