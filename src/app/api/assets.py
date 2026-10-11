"""What the Equipment, Sensor Trends, Alarms and Maintenance pages read, from the Gold tables.

Gold has no health score, remaining useful life, sensor limits, acknowledgements, technician or work-order
title: those fields are left out (or built from Gold fields, say so in the field) and never invented.
Asset status is one SQL definition (asset_cte) used by the Equipment list and the Fleet Health counts.
"""

from app.api.db import GoldDB
from app.api.errors import ApiError
from app.api.kpis import KPI_DEFINITIONS, Kpi
from app.api.schemas import (
    Alarm,
    AssetDetail,
    AssetKpis,
    AssetPage,
    AssetRow,
    AssetStatus,
    LatestReading,
    Sensor,
    SensorPoint,
    SensorRange,
    SensorSeries,
    WorkOrderItem,
    WorkOrderStatus,
)
from app.api.shaping import kpi_rows, scale, utc

# Status from availability over the period (thresholds = the report targets). Gold has no warning/critical
# concept of its own, so this rule is the definition. Decommissioned and standby assets are offline.
OPERATIONAL_FROM = 0.95
WARNING_FROM = 0.90
OFFLINE_STATUSES = ("standby", "decommissioned")
STATUS_PARAMS = {"operational": OPERATIONAL_FROM, "warning": WARNING_FROM}

_STATUS_ORDER = "CASE asset_status WHEN 'critical' THEN 0 WHEN 'warning' THEN 1 WHEN 'offline' THEN 2 ELSE 3 END"
_ASSET_ID = "equipment_id = :equipment_id"


def asset_cte(db: GoldDB) -> str:
    """SQL prefix defining `assets`: one row per real equipment with its status. Needs STATUS_PARAMS."""
    offline = ", ".join(f"'{s}'" for s in OFFLINE_STATUSES)
    return f"""
    WITH availability AS (
        SELECT k.equipment_key, {KPI_DEFINITIONS[Kpi.availability][1]} AS availability, SUM(k.alarm_count) AS alarms
        FROM {db.table("kpi_equipment_month")} k
        WHERE k.equipment_key <> -1
        GROUP BY k.equipment_key
    ), last_maintenance AS (
        SELECT w.equipment_key, MAX(w.closed_ts) AS last_maintenance
        FROM {db.table("fact_maintenance_work_order")} w
        WHERE w.status = 'Closed'
        GROUP BY w.equipment_key
    ), assets AS (
        SELECT e.equipment_key, e.equipment_id, e.equipment_name, e.manufacturer, e.production_line, e.criticality,
               e.install_date, s.site_name, t.equipment_type, a.availability, COALESCE(a.alarms, 0) AS alarms,
               m.last_maintenance,
               CASE WHEN LOWER(e.status) IN ({offline}) OR a.availability IS NULL THEN 'offline'
                    WHEN a.availability >= :operational THEN 'operational'
                    WHEN a.availability >= :warning THEN 'warning'
                    ELSE 'critical' END AS asset_status
        FROM {db.table("dim_equipment")} e
        LEFT JOIN availability a ON a.equipment_key = e.equipment_key
        LEFT JOIN last_maintenance m ON m.equipment_key = e.equipment_key
        LEFT JOIN {db.table("dim_site")} s ON e.site_key = s.site_key
        LEFT JOIN {db.table("dim_equipment_type")} t ON e.equipment_type_key = t.equipment_type_key
        WHERE e.equipment_key <> -1
    )"""


def _asset(r: dict) -> AssetRow:
    return AssetRow(
        id=r["equipment_id"],
        name=r["equipment_name"],
        type=r["equipment_type"],
        line=r["production_line"],
        site=r["site_name"],
        status=r["asset_status"],
        availability_pct=scale(r["availability"], 100),
        manufacturer=r["manufacturer"],
        criticality=r["criticality"],
        installed=r["install_date"],
        active_alarms=int(r["alarms"]),
        last_maintenance=utc(r["last_maintenance"]),
    )


def list_assets(
    db: GoldDB,
    status: AssetStatus | None = None,
    q: str | None = None,
    has_sensors: bool = False,
    page: int = 1,
    page_size: int = 50,
) -> AssetPage:
    """Most urgent first (critical, warning, offline, operational), then by id."""
    where, params = [], dict(STATUS_PARAMS)
    if status:
        where.append("asset_status = :status")
        params["status"] = status
    if q:
        where.append(
            "(LOWER(equipment_id) LIKE :q OR LOWER(equipment_name) LIKE :q"
            " OR LOWER(equipment_type) LIKE :q OR LOWER(production_line) LIKE :q)"
        )
        params["q"] = f"%{q.strip().lower()}%"
    if has_sensors:
        where.append(f"equipment_key IN (SELECT equipment_key FROM {db.table('fact_sensor_reading')})")
    sql = (
        asset_cte(db)
        + " SELECT *, COUNT(*) OVER () AS total FROM assets"
        + (" WHERE " + " AND ".join(where) if where else "")
        + f" ORDER BY {_STATUS_ORDER}, equipment_id LIMIT {page_size} OFFSET {(page - 1) * page_size}"
    )
    rows = db.query(sql, params)
    return AssetPage(
        items=[_asset(r) for r in rows],
        total=int(rows[0]["total"]) if rows else 0,
        page=page,
        page_size=page_size,
    )


_READING_STATUS: dict[str, AssetStatus] = {"normal": "operational", "warning": "warning", "alarm": "critical"}


def asset_detail(db: GoldDB, equipment_id: str) -> AssetDetail:
    found = db.query(
        asset_cte(db) + f" SELECT * FROM assets WHERE {_ASSET_ID}",
        {**STATUS_PARAMS, "equipment_id": equipment_id},
    )
    if not found:
        raise ApiError(404, "not_found", f"Equipment {equipment_id} not found")
    kpis = kpi_rows(
        db,
        [Kpi.availability, Kpi.mtbf_hours, Kpi.mttr_hours, Kpi.unplanned_downtime_hours],
        filters={"equipment_ids": [equipment_id]},
    )
    values = kpis.rows[0] if kpis.rows else {}
    readings = db.query(
        f"""
        SELECT sensor_id, sensor_type, canonical_unit, reading_value, alarm_state
        FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY sensor_id ORDER BY event_ts DESC) AS n
              FROM {db.table("fact_sensor_reading")}
              WHERE equipment_key = (SELECT equipment_key FROM {db.table("dim_equipment")} WHERE {_ASSET_ID}))
        WHERE n = 1
        ORDER BY sensor_id
        """,
        {"equipment_id": equipment_id},
    )
    return AssetDetail(
        **_asset(found[0]).model_dump(),
        kpis=AssetKpis(
            availability_pct=scale(values.get("availability"), 100),
            mtbf_h=scale(values.get("mtbf_hours")),
            mttr_h=scale(values.get("mttr_hours")),
            unplanned_downtime_h=scale(values.get("unplanned_downtime_hours")),
        ),
        latest_readings=[
            LatestReading(
                sensor=(r["sensor_type"] or r["sensor_id"]).title(),
                value=r["reading_value"],
                unit=r["canonical_unit"],
                status=_READING_STATUS.get(r["alarm_state"], "operational"),
            )
            for r in readings
        ],
    )


# ---------------------------------------------------------------- sensors
# Hours before the asset's last reading. The data ends on a fixed date (as_of), so a window counted back from
# "now" would be empty; "all" is the whole period (about 120 readings per sensor, no downsampling needed).
RANGE_HOURS = {"1h": 1, "24h": 24, "7d": 168, "30d": 720, "all": None}


def sensor_series(db: GoldDB, equipment_id: str, range_: SensorRange) -> SensorSeries:
    key = db.query(
        f"SELECT equipment_key FROM {db.table('dim_equipment')} WHERE {_ASSET_ID}", {"equipment_id": equipment_id}
    )
    if not key:
        raise ApiError(404, "not_found", f"Equipment {equipment_id} not found")
    fact = db.table("fact_sensor_reading")
    hours = RANGE_HOURS[range_]  # from a fixed list: the only value formatted into the SQL
    window = (
        ""
        if hours is None
        else f"AND event_ts >= TIMESTAMPADD(HOUR, -{hours}, (SELECT MAX(event_ts) FROM {fact} WHERE equipment_key = :key))"
    )
    rows = db.query(
        f"""
        SELECT sensor_id, sensor_type, canonical_unit, event_ts, reading_value, is_alarm
        FROM {fact}
        WHERE equipment_key = :key {window}
        ORDER BY sensor_id, event_ts
        """,
        {"key": key[0]["equipment_key"]},
    )
    sensors: dict[str, Sensor] = {}
    for r in rows:
        sensor = sensors.setdefault(
            r["sensor_id"],
            Sensor(id=r["sensor_id"], name=(r["sensor_type"] or r["sensor_id"]).title(), unit=r["canonical_unit"], points=[]),
        )
        sensor.points.append(SensorPoint(t=utc(r["event_ts"]), value=r["reading_value"], anomaly=bool(r["is_alarm"])))
    return SensorSeries(asset_id=equipment_id, range=range_, sensors=list(sensors.values()))


# ---------------------------------------------------------------- alarms
_ALARM_NAMES = {
    "VIB_HIGH": "Vibration high",
    "TMP_HIGH": "Temperature high",
    "PRS_HIGH": "Pressure high",
    "CUR_HIGH": "Motor current high",
}


def list_alarms(db: GoldDB, equipment_id: str | None = None, limit: int = 200) -> list[Alarm]:
    """Alarm readings, newest first. Gold keeps no acknowledged/cleared state, so every alarm is listed as open."""
    where, params = ["is_alarm = TRUE", "equipment_key <> -1"], {}
    if equipment_id:
        where.append(_ASSET_ID)
        params["equipment_id"] = equipment_id
    rows = db.query(
        f"""
        SELECT event_ts, equipment_id, sensor_id, sensor_type, reading_value, canonical_unit, alarm_state, alarm_code
        FROM {db.table("fact_sensor_reading")}
        WHERE {" AND ".join(where)}
        ORDER BY event_ts DESC
        LIMIT {limit}
        """,
        params,
    )
    alarms = []
    for r in rows:
        critical = r["alarm_state"] == "alarm"
        level = "alarm" if critical else "warning"
        known = _ALARM_NAMES.get(r["alarm_code"])
        ts = utc(r["event_ts"])
        alarms.append(
            Alarm(
                id=f"{r['sensor_id']}|{int(ts.timestamp())}",
                asset_id=r["equipment_id"],
                severity="critical" if critical else "warning",
                sensor=r["sensor_type"],
                message=f"{known} ({level} level)" if known else f"{(r['sensor_type'] or 'Sensor').title()} {level}",
                value=r["reading_value"],
                unit=r["canonical_unit"],
                time=ts,
            )
        )
    return alarms


# ---------------------------------------------------------------- work orders
def list_work_orders(
    db: GoldDB, status: WorkOrderStatus | None = None, equipment_id: str | None = None, limit: int = 100
) -> list[WorkOrderItem]:
    """Overdue first, then open, then the rest, newest first. Status: completed = Closed, overdue = past due and
    still open, open = Open or In Progress."""
    where, params = ["w.equipment_key <> -1"], {}
    if equipment_id:
        where.append("w.equipment_id = :equipment_id")
        params["equipment_id"] = equipment_id
    if status:
        params["status"] = status
    rows = db.query(
        f"""
        SELECT * FROM (
            SELECT w.work_order_id, w.equipment_id, w.priority,
                   CASE WHEN w.status = 'Closed' THEN 'completed' WHEN w.status = 'Cancelled' THEN 'cancelled'
                        WHEN w.is_overdue THEN 'overdue' ELSE 'open' END AS wo_status,
                   t.maintenance_type, f.failure_mode_code, f.failure_mode_desc,
                   w.created_ts, w.due_ts, w.closed_ts, d.downtime_hours, w.maintenance_cost_eur
            FROM {db.table("fact_maintenance_work_order")} w
            LEFT JOIN {db.table("dim_maintenance_type")} t ON w.maintenance_type_key = t.maintenance_type_key
            LEFT JOIN {db.table("dim_failure_mode")} f ON w.failure_mode_key = f.failure_mode_key
            LEFT JOIN {db.table("fact_downtime")} d ON d.work_order_id = w.work_order_id
            WHERE {" AND ".join(where)}
        ) x
        {"WHERE wo_status = :status" if status else ""}
        ORDER BY CASE wo_status WHEN 'overdue' THEN 0 WHEN 'open' THEN 1 ELSE 2 END, created_ts DESC
        LIMIT {limit}
        """,
        params,
    )
    return [
        WorkOrderItem(
            id=r["work_order_id"],
            asset_id=r["equipment_id"],
            type=r["maintenance_type"],
            status=r["wo_status"],
            priority=r["priority"],
            title=_title(r),
            opened=utc(r["created_ts"]),
            due=utc(r["due_ts"]),
            completed=utc(r["closed_ts"]),
            downtime_h=None if r["downtime_hours"] is None else float(r["downtime_hours"]),
            cost=None if r["maintenance_cost_eur"] is None else float(r["maintenance_cost_eur"]),
        )
        for r in rows
    ]


def _title(r: dict) -> str:
    kind = r["maintenance_type"] or "Work order"
    if r["failure_mode_code"] in (None, "NONE"):
        return f"{kind} maintenance"
    return f"{kind} – {r['failure_mode_desc']}"