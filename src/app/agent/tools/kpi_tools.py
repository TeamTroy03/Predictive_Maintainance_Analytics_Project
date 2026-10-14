"""Typed tool returning governed KPIs (availability, MTBF, MTTR, downtime, cost...) from the Gold layer.
The LLM only picks a KPI and filters; the value is calculated in Databricks: the additive pieces of
`gold_kpi_equipment_month` are summed over the selected rows, then the formula (same as KPI_FORMULAS in the Gold
notebook) is applied. Formulas come only from KPIS below and user values are only bound parameters."""

import logging
import os
import re
import time
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

logger = logging.getLogger("app.agent")  # same log file as agent.py (configured there)

DATA_DATE_CACHE_SECONDS = int(os.getenv("DATA_DATE_CACHE_SECONDS", "600"))  # how long the last data date is reused
DATA_DATE_RETRY_SECONDS = 60  # after a failed or empty lookup, wait this long before asking Databricks again

_AS_OF = "Overdue is a snapshot at as_of_ts, bucketed by the month the work order was due."
_ALARM = "Counts alarm READINGS (not distinct events), so it grows with the number of sensors per asset."
_FAIL = "Failures count in the month they started, downtime hours are split across months: values near the period edges can be distorted."
_DEN = "Undefined (None) when the denominator is zero, e.g. no unplanned failures in the selection."
_MONTHLY = "Monthly grain only; the latest month is partial (it stops at as_of_ts)."

# kpi -> formula (verbatim from the Gold notebook), unit, definition, limitations
KPIS = {
    "availability": {
        "formula": "(SUM(calendar_hours) - SUM(downtime_hours)) / NULLIF(SUM(calendar_hours), 0)",
        "unit": "ratio (0-1)", "definition": "Share of calendar hours the equipment was not down.",
        "limitations": [_MONTHLY, "Downtime includes planned and unplanned downtime."]},
    "unplanned_downtime_hours": {
        "formula": "SUM(unplanned_downtime_hours)", "unit": "hours",
        "definition": "Hours of downtime caused by unplanned (corrective) failures.", "limitations": [_FAIL]},
    "unplanned_failures": {
        "formula": "SUM(unplanned_failure_count)", "unit": "count",
        "definition": "Number of unplanned downtime events that started in the period.", "limitations": [_MONTHLY]},
    "mtbf_hours": {
        "formula": "SUM(operating_uptime_hours) / NULLIF(SUM(unplanned_failure_count), 0)", "unit": "hours",
        "definition": "Mean operating uptime between unplanned failures.", "limitations": [_FAIL, _DEN],
        "note": "OPEN DECISION: uses operating_uptime_hours (scaled by operating_share), not calendar uptime."},
    "mttr_hours": {
        "formula": "SUM(unplanned_downtime_hours) / NULLIF(SUM(unplanned_failure_count), 0)", "unit": "hours",
        "definition": "Mean unplanned downtime hours per unplanned failure.", "limitations": [_FAIL, _DEN]},
    "overdue_work_orders": {
        "formula": "SUM(overdue_work_order_count)", "unit": "count",
        "definition": "Work orders still open after their due date.", "limitations": [_AS_OF]},
    "alarm_rate": {
        "formula": "SUM(alarm_count) / NULLIF(SUM(asset_days), 0)", "unit": "readings per asset-day",
        "definition": "Warning and alarm readings per asset-day.", "limitations": [_ALARM, _MONTHLY]},
    "alarm_warning_rate": {
        "formula": "SUM(alarm_warning_count) / NULLIF(SUM(asset_days), 0)", "unit": "readings per asset-day",
        "definition": "Warning-level readings per asset-day.", "limitations": [_ALARM, _MONTHLY]},
    "alarm_critical_rate": {
        "formula": "SUM(alarm_critical_count) / NULLIF(SUM(asset_days), 0)", "unit": "readings per asset-day",
        "definition": "Alarm-level (critical) readings per asset-day.", "limitations": [_ALARM, _MONTHLY]},
    "maintenance_cost_eur": {
        "formula": "SUM(maintenance_cost_eur)", "unit": "EUR",
        "definition": "Labor plus parts cost of work orders.",
        "limitations": ["Bucketed by COALESCE(closed_ts, created_ts), so open work orders already contribute cost."]},
    "repeat_failure_rate": {
        "formula": "SUM(repeat_failure_count) / NULLIF(SUM(unplanned_failure_count), 0)", "unit": "ratio (0-1)",
        "definition": "Share of unplanned failures repeating the same equipment and failure mode within 30 days.",
        "limitations": [_FAIL, _DEN]},
    "work_orders": {
        "formula": "SUM(work_order_count)", "unit": "count",
        "definition": "Work orders created in the period.", "limitations": [_MONTHLY]},
}
KpiName = Literal[*KPIS]

# request field -> (key column, Gold dimension, condition). Gold stores codes and IDs upper-cased.
FILTERS = {
    "equipment_type": ("equipment_type_key", "dim_equipment_type", "LOWER(equipment_type) = LOWER(:equipment_type)"),
    "equipment_id": ("equipment_key", "dim_equipment", "equipment_id = UPPER(:equipment_id)"),
}


class KpiRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    kpi_name: KpiName
    start_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    end_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")  # inclusive
    equipment_type: str | None = Field(None, pattern=r"^[A-Za-z0-9][A-Za-z0-9 _/().-]{0,63}$")
    equipment_id: str | None = Field(None, pattern=r"^[A-Za-z0-9_-]{1,32}$")

    @model_validator(mode="after")
    def _end_not_before_start(self):
        if self.end_month < self.start_month:  # YYYY-MM strings sort like dates
            raise ValueError("end_month must not be before start_month")
        return self


def _table(name: str) -> str:
    """Gold table name from config, e.g. team3.gold.test_gold_dim_site."""
    table = f"{os.environ['GOLD_CATALOG']}.{os.environ['GOLD_SCHEMA']}.{os.getenv('GOLD_TABLE_PREFIX', '')}gold_{name}"
    if not re.fullmatch(r"[\w.]+", table):
        raise ValueError("GOLD_CATALOG, GOLD_SCHEMA and GOLD_TABLE_PREFIX may only contain letters, digits and _")
    return table


def run_query(sql: str, params: dict) -> dict:
    """Run one SELECT on Databricks (named :params) and return its single row as a dict."""
    # imported here so the module loads without the connector installed
    from databricks import sql as dbsql

    with dbsql.connect(
        server_hostname=os.environ["DATABRICKS_HOST"],
        http_path=os.environ["DATABRICKS_HTTP_PATH"],
        access_token=os.environ["DATABRICKS_TOKEN"],
    ) as conn, conn.cursor() as cursor:
        cursor.execute(sql, params)
        return cursor.fetchone().asDict()


_data_end = {"info": None, "valid_until": 0.0}  # cache of data_available_until(): the answer and when to ask again


def data_available_until(run=run_query) -> dict | None:
    """Last date the Gold data covers, from Databricks: {"as_of": "YYYY-MM-DD", "last_month": "YYYY-MM"}, or None when it
    cannot be read. `as_of_ts` is the Gold run's AS_OF (latest timestamp in Silver sensor events and work orders) and the
    KPI months run up to it. Cached for DATA_DATE_CACHE_SECONDS; never raises, so a Databricks outage cannot stop the
    planner (it only loses the date, and is not asked again for DATA_DATE_RETRY_SECONDS)."""
    now = time.monotonic()
    if now < _data_end["valid_until"]:
        return _data_end["info"]
    info = None
    try:
        row = run("SELECT MAX(as_of_ts) AS as_of_ts, MAX(month_start_date_key) AS last_month_key "
                  f"FROM {_table('kpi_equipment_month')}", {})
        if row["as_of_ts"] is not None and row["last_month_key"] is not None:
            month = str(row["last_month_key"])  # yyyyMMdd of the first day of the month
            info = {"as_of": str(row["as_of_ts"])[:10], "last_month": f"{month[:4]}-{month[4:6]}"}
    except Exception as e:  # BLE001: Databricks down, bad token, table missing...
        logger.warning("data_date_unavailable", extra={"fields": {"error": type(e).__name__}})
    _data_end.update(info=info, valid_until=now + (DATA_DATE_CACHE_SECONDS if info else DATA_DATE_RETRY_SECONDS))
    return info


def compute_kpi(request: KpiRequest, run=run_query) -> dict:
    """Run one KPI for a month range and optional filters; returns the value with its evidence."""
    kpi, kpi_table = KPIS[request.kpi_name], _table("kpi_equipment_month")
    # Unknown members (-1) are never part of a KPI; month keys are the first day of the month, yyyyMMdd
    where = ["month_start_date_key BETWEEN :start AND :end",
             "equipment_key <> -1", "site_key <> -1", "equipment_type_key <> -1"]
    params = {"start": int(request.start_month.replace("-", "") + "01"),
              "end": int(request.end_month.replace("-", "") + "01")}
    for field, (key, dim, condition) in FILTERS.items():
        if value := getattr(request, field):
            where.append(f"{key} IN (SELECT {key} FROM {_table(dim)} WHERE {condition})")
            params[field] = value
    sql = (f"SELECT {kpi['formula']} AS value, COUNT(*) AS row_count, MAX(as_of_ts) AS as_of_ts, "
           f"MAX(gold_loaded_ts) AS gold_loaded_ts FROM {kpi_table} WHERE " + " AND ".join(where))

    row = run(sql, params)
    limitations = list(kpi["limitations"])
    if row["row_count"] == 0:
        limitations.append("No Gold rows match this period and filters; check the months and the spelling of the filters.")
    elif row["value"] is None:
        limitations.append("The denominator is zero for this selection, so the value is undefined (not 0).")
    return {
        "kpi_name": request.kpi_name,
        "value": None if row["value"] is None else float(row["value"]),
        "unit": kpi["unit"],
        "filters": request.model_dump(exclude={"kpi_name"}, exclude_none=True),
        "source_measure": f"{kpi_table}: {kpi['formula']}" + (f" | {kpi['note']}" if "note" in kpi else ""),
        "as_of_ts": None if row["as_of_ts"] is None else str(row["as_of_ts"]),
        "gold_loaded_ts": None if row["gold_loaded_ts"] is None else str(row["gold_loaded_ts"]),
        "row_count": row["row_count"],
        "limitations": limitations,
    }


@tool
def get_kpi(kpi_name: KpiName, start_month: str, end_month: str,
            equipment_type: str | None = None, equipment_id: str | None = None) -> dict:
    """Return one governed KPI value from the Gold layer, calculated in the database (never estimate it yourself).
    Months are YYYY-MM, both inclusive. Optional filters: equipment_type, equipment_id.
    The result has the value, unit, applied filters, source_measure, as_of_ts, row_count and limitations."""
    try:
        return compute_kpi(KpiRequest(kpi_name=kpi_name, start_month=start_month, end_month=end_month,
                                      equipment_type=equipment_type, equipment_id=equipment_id))
    except ValidationError as e:
        return {"error": f"Invalid KPI request: {e.errors(include_url=False, include_input=False)}"}
