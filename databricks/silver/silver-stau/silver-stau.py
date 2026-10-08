# Databricks notebook source
"""Silver layer for work orders: bronze_work_orders -> silver_work_orders.

Implements silver_work_orders_rules.md. Layout:
    1. CONFIG                 parameters, codes, rules, Silver schema
    2. clean_worker_ord       cleaning tools: Bronze rows -> cleaned rows with _f_<RULE> flags
    3. creating_silver_table  rule counts, error gate, MERGE into Silver and quarantine
    4. main                   widgets, read Bronze, run parts 2 and 3
Needs Spark 3.5+ (Databricks Runtime 14.3+): try_to_timestamp, nullif.
"""

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from functools import reduce
from operator import add, or_

from pyspark.sql import Column, DataFrame, SparkSession, Window
from pyspark.sql import functions as F

# ======== 1. CONFIG ===============================================================================
TABLE_NAMES = {
    "bronze": "bronze_work_orders",  # the only input
    "silver": "silver_work_orders",
    "quarantine": "silver_dq_quarantine",
    "audit": "dq_results",
}
QUARANTINE_ENTITY = "work_orders"
BRONZE_LOADED_STATUS = "SUCCESS"
BRONZE_COLUMN_MAP = {
    "batch_id": "_batch_id",
    "source_batch_id": "batch_id",
    "ingestion_timestamp": "_ingestion_ts",
    "source_filename": "_source_file",
    "record_hash": "_record_hash",
    "load_status": "_load_status",
}
TIMESTAMP_PATTERN = "yyyy-MM-dd'T'HH:mm:ss'Z'"
NULL_MARKER = "<NULL>"
# Labour and currency (Decimal or str, never float)
STANDARD_LABOUR_RATE_EUR_H = Decimal(
    "55"
)  # domain assumption for L1/L2/LC1, never stored
LABOUR_SENTINELS = [9999]
LABOUR_MATCH_TOLERANCE_H = Decimal("0.01")  # EUR rows
COST_MATCH_TOLERANCE_EUR = Decimal("0.01")  # EUR rows
NON_EUR_COST_TOLERANCE_PCT = 1.5  # GBP/USD rows: the fixed rate is approximate
FX_RATE_SOURCE = "FIXED_RATE"  # OPEN: confirm official rates with the instructor
FX_RATES_TO_EUR = {
    "EUR": Decimal("1.0000"),
    "GBP": Decimal("1.169"),
    "USD": Decimal("0.903"),
}

SLA_HOURS = {"P1": 24, "P2": 72, "P3": 168, "P4": 336}
PLANNED_WO_TYPES = ["Preventive", "Inspection"]
UNPLANNED_WO_TYPES = ["Corrective"]
RECOVERY_RECORD_VERSION = 1  # closed_ts is only recovered on version 1

CODES = {
    "wo_type": ["Preventive", "Corrective", "Inspection", "Predictive"],
    "priority": ["P1", "P2", "P3", "P4"],
    "status": ["Open", "In Progress", "Closed", "Cancelled"],
    "downtime_type": ["planned", "unplanned"],
    "currency": ["EUR", "GBP", "USD"],
    "requested_by_role": [
        "Planner",
        "Operator",
        "Shift Supervisor",
        "Condition Monitoring",
    ],
    "cancellation_reason": [
        "Scope merged into next PM",
        "Raised in error",
        "Asset not available - production priority",
    ],
    "failure_mode_code": [
        "NONE",
        "BRG-WEAR",
        "MISALIGN",
        "IMBAL",
        "OVERHEAT",
        "LUB-FAIL",
        "WIND-INS",
        "SEAL-LEAK",
        "CAVIT",
        "VALVE-FAIL",
        "GEAR-WEAR",
        "CLOG",
    ],
    "root_cause_code": [
        "RC-WEAR",
        "RC-CONTAM",
        "RC-LUB",
        "RC-INST",
        "RC-OPER",
        "RC-MAINT",
        "RC-DESIGN",
        "RC-MATL",
    ],
}
CHECK_ONLY_WHEN_SET = [
    "downtime_type",
    "root_cause_code",
    "cancellation_reason",
    "work_order_id",
]
PATTERNS = {
    "work_order_id": r"^WO-[0-9]{5}$",
    "assigned_technician_id": r"^TECH-[0-9]{2}$",
}
TRIGGER_PATTERN = r"^SE-[0-9]{6}$"
FAILURE_MODE_DESC = {
    "NONE": "No failure (planned work)",
    "BRG-WEAR": "Bearing wear",
    "MISALIGN": "Shaft misalignment",
    "IMBAL": "Rotor imbalance",
    "OVERHEAT": "Overheating",
    "LUB-FAIL": "Lubrication failure",
    "WIND-INS": "Stator winding insulation breakdown",
    "SEAL-LEAK": "Mechanical seal leakage",
    "CAVIT": "Cavitation",
    "VALVE-FAIL": "Valve failure",
    "GEAR-WEAR": "Gear tooth wear",
    "CLOG": "Blocked suction / plugged strainer",
}

# Columns: 27 business + 5 source lineage + Bronze audit columns (after renaming)
BUSINESS_COLS = [
    "work_order_id",
    "equipment_id",
    "wo_type",
    "priority",
    "failure_mode_code",
    "failure_mode_desc",
    "description",
    "requested_by_role",
    "assigned_technician_id",
    "created_ts",
    "due_ts",
    "actual_start_ts",
    "closed_ts",
    "downtime_start_ts",
    "downtime_end_ts",
    "downtime_minutes",
    "downtime_type",
    "status",
    "cancellation_reason",
    "root_cause_code",
    "triggering_event_id",
    "labor_hours",
    "labor_cost",
    "parts_cost",
    "currency",
    "last_modified_ts",
    "record_version",
]
LINEAGE_COLS = [
    "source_file",
    "ingestion_ts",
    "batch_id",
    "source_system",
    "source_system_key",
]
BRONZE_AUDIT_COLS = [
    "_batch_id",
    "_ingestion_ts",
    "_source_file",
    "_source_system",
    "_record_hash",
    "_load_status",
]
REQUIRED_COLS = BUSINESS_COLS + LINEAGE_COLS + BRONZE_AUDIT_COLS
TIMESTAMP_COLS = [
    "created_ts",
    "due_ts",
    "actual_start_ts",
    "closed_ts",
    "downtime_start_ts",
    "downtime_end_ts",
    "last_modified_ts",
    "ingestion_ts",
    "_ingestion_ts",
]
EVENT_TS_COLS = [
    "created_ts",
    "actual_start_ts",
    "closed_ts",
    "downtime_start_ts",
    "downtime_end_ts",
    "last_modified_ts",
]  # due_ts is a deadline, not an event
DOWNTIME_COLS = [
    "downtime_start_ts",
    "downtime_end_ts",
    "downtime_minutes",
    "downtime_type",
]
NUMERIC_TYPES = {
    "labor_hours": "decimal(8,2)",
    "labor_cost": "decimal(12,2)",
    "parts_cost": "decimal(12,2)",
    "downtime_minutes": "int",
    "record_version": "int",
}

# Value written in the *_fix_rule columns for each rule that changed a value
TS_FIX_LABELS = {
    "TS_CLOSED_RECOVERED": "CLOSED_TS_FROM_LAST_MODIFIED",
    "TS_CLOSED_UNRECOVERABLE": "CLOSED_TS_UNRECOVERABLE",
}
LABOUR_FIX_LABELS = {
    "L1": "SENTINEL_RECOVERED_FROM_COST",
    "L2": "NEGATIVE_ABS_VERIFIED_BY_COST",
    "L3": "LABOR_HOURS_UNRECOVERABLE",
    "LC1": "NEG_COST_RECOVERED_FROM_HOURS",
}
PARTS_FIX_LABELS = {"P1": "NEG_PARTS_NULLED"}

# rule -> (dimension, severity, description). Adding a rule = one line here + one _f_<rule> boolean
# column in a cleaning step (except DEDUP_RULES). ERROR rules above 0 stop the run before any write.
DEDUP_RULES = ["DUP_EXACT", "VERSION_LATEST"]
RULES = {
    "DUP_EXACT": ("uniqueness", "DROP", "Exact duplicate of the business columns"),
    "VERSION_LATEST": ("uniqueness", "DROP", "Superseded record version"),
    "TYPE_CAST": ("validity", "ERROR", "Non-empty value that does not parse"),
    "MISSING_WORK_ORDER_ID": (
        "completeness",
        "ROW_QUARANTINE",
        "work_order_id missing",
    ),
    "MISSING_CREATED_TS": ("completeness", "ROW_QUARANTINE", "created_ts missing"),
    "SOURCE_KEY_FORMAT": (
        "validity",
        "ERROR",
        "source_system_key <> system|work_order_id",
    ),
    "DOMAIN_CODES": (
        "validity",
        "ERROR",
        "Code outside its list or ID with a wrong format",
    ),
    "FM_DESC_MAP": (
        "consistency",
        "ERROR",
        "failure_mode_desc does not match failure_mode_code",
    ),
    "FM_NONE_IFF_PLANNED_TYPE": (
        "consistency",
        "ERROR",
        "NONE <=> Preventive or Inspection",
    ),
    "RC_NULL_IFF_NOT_FAILURE_CLOSED": (
        "consistency",
        "ERROR",
        "root cause null <=> planned/open",
    ),
    "DUE_MATCHES_PRIORITY": (
        "consistency",
        "ERROR",
        "due_ts is not created_ts + SLA of priority",
    ),
    "CANCEL_REASON_IFF_CANCELLED": ("consistency", "ERROR", "reason set <=> Cancelled"),
    "STATUS_LIFECYCLE": (
        "consistency",
        "ERROR",
        "Field pattern does not match the status",
    ),
    "TS_CLOSED_RECOVERED": (
        "consistency",
        "FIX",
        "closed_ts < created_ts, from last_modified",
    ),
    "TS_CLOSED_UNRECOVERABLE": (
        "consistency",
        "FIELD_QUARANTINE",
        "closed_ts wrong, no safe fix",
    ),
    "TS_ORDER": ("consistency", "ERROR", "created/start/downtime end after closed_ts"),
    "TS_MODIFIED_BEFORE_CLOSE": (
        "consistency",
        "ERROR",
        "last_modified_ts before closed_ts",
    ),
    "TS_AFTER_EXTRACT": ("validity", "WARN", "Event timestamp after ingestion_ts"),
    "START_BEFORE_CREATED": (
        "consistency",
        "WARN",
        "Emergency start before work order creation",
    ),
    "FX_UNAVAILABLE": (
        "validity",
        "FIELD_QUARANTINE",
        "No fixed rate for the currency",
    ),
    "L1": ("validity", "FIX", "Labour hours sentinel recovered from labor_cost"),
    "L2": ("validity", "FIX", "Negative labour hours made positive, verified by cost"),
    "L3": ("validity", "FIELD_QUARANTINE", "Labour hours not recoverable"),
    "LC1": ("validity", "FIX", "Negative labour cost recovered from hours"),
    "LABOR_COST_CONSISTENT": (
        "consistency",
        "ERROR",
        "labor_cost is not hours x standard rate",
    ),
    "P1": (
        "validity",
        "FIELD_QUARANTINE",
        "Negative parts cost nulled (recovery is Gold)",
    ),
    "EQ_MISSING": (
        "completeness",
        "FIELD_QUARANTINE",
        "equipment_id missing, kept null (Gold)",
    ),
    "DT_COMPLETE": ("completeness", "ERROR", "Downtime fields not all set or all null"),
    "DT_MINUTES_MATCH": (
        "consistency",
        "ERROR",
        "downtime_minutes differs from end - start",
    ),
    "DT_TYPE_MATCHES_WO_TYPE": ("consistency", "ERROR", "unplanned <=> Corrective"),
    "DT_TIMING": ("consistency", "WARN", "Downtime start does not fit its type"),
    "DT_OVERLAP": (
        "consistency",
        "FLAG",
        "Downtime overlaps an earlier one on the same asset",
    ),
    "TRIGGER_FORMAT": ("validity", "ERROR", "Sensor trigger format, Predictive only"),
}

# rule -> (type, [(field set to null, column holding its value as delivered)]).
# ROW rules keep the row out of Silver (field_name stays null, original_values = whole row).
QUARANTINE_RULES = {
    "MISSING_WORK_ORDER_ID": ("ROW", []),
    "MISSING_CREATED_TS": ("ROW", []),
    "EQ_MISSING": ("FIELD", [("equipment_id", "equipment_id")]),
    "P1": ("FIELD", [("parts_cost_eur", "parts_cost")]),
    "TS_CLOSED_UNRECOVERABLE": ("FIELD", [("closed_ts", "_orig_closed_ts")]),
    "L3": ("FIELD", [("labor_hours", "_orig_labor_hours")]),
    "FX_UNAVAILABLE": (
        "FIELD",
        [("labor_cost_eur", "labor_cost"), ("parts_cost_eur", "parts_cost")],
    ),
}
ROW_QUARANTINE_RULES = [
    rule for rule, (kind, _) in QUARANTINE_RULES.items() if kind == "ROW"
]

# Silver table: (column, type, comment). Also drives casting, the row hash and the DDL.
SILVER_SCHEMA = [
    ("work_order_id", "string", "WO-nnnnn, primary key"),
    (
        "equipment_id",
        "string",
        "Asset as delivered; null when missing in source (EQ_MISSING)",
    ),
    ("wo_type", "string", "Preventive, Corrective, Inspection, Predictive"),
    ("priority", "string", "P1 to P4"),
    ("failure_mode_code", "string", "NONE = planned work"),
    ("failure_mode_desc", "string", "Description of failure_mode_code"),
    ("description", "string", "Work order text"),
    (
        "requested_by_role",
        "string",
        "Planner, Operator, Shift Supervisor, Condition Monitoring",
    ),
    ("assigned_technician_id", "string", "TECH-nn, pseudonymous"),
    ("created_ts", "timestamp", "UTC"),
    ("due_ts", "timestamp", "created_ts + SLA of the priority"),
    ("actual_start_ts", "timestamp", "Null = not started (Open, Cancelled)"),
    ("closed_ts", "timestamp", "Null = not finished; fixed per TS_CLOSED_RECOVERED"),
    ("downtime_start_ts", "timestamp", "Null = no downtime"),
    ("downtime_end_ts", "timestamp", "Null = no downtime or still running"),
    ("downtime_minutes", "int", "Null = no downtime or still running"),
    ("downtime_type", "string", "planned or unplanned"),
    ("status", "string", "Open, In Progress, Closed, Cancelled"),
    ("cancellation_reason", "string", "Set only when Cancelled"),
    ("root_cause_code", "string", "Null = planned work or not yet closed"),
    (
        "triggering_event_id",
        "string",
        "Sensor event that raised a predictive work order",
    ),
    ("labor_hours", "decimal(8,2)", "Booked person-hours, cleaned per L1/L2"),
    ("labor_cost_eur", "decimal(12,2)", "Labour cost in EUR at the fixed rate"),
    (
        "parts_cost_eur",
        "decimal(12,2)",
        "Parts cost EUR; null if not closed or negative",
    ),
    ("currency", "string", "Source currency"),
    ("fx_rate_to_eur", "decimal(8,4)", "Fixed rate applied (1 for EUR)"),
    ("last_modified_ts", "timestamp", "Last change in CMMS, UTC"),
    ("record_version", "int", "Version kept"),
    (
        "ts_fix_rule",
        "string",
        "CLOSED_TS_FROM_LAST_MODIFIED, CLOSED_TS_UNRECOVERABLE or UNCHANGED",
    ),
    (
        "labor_fix_rule",
        "string",
        "Labour rules applied, joined by semicolon, or UNCHANGED",
    ),
    ("parts_fix_rule", "string", "NEG_PARTS_NULLED or UNCHANGED"),
    ("equipment_resolution", "string", "SOURCE or MISSING"),
    (
        "dq_downtime_overlap",
        "boolean",
        "Downtime overlaps an earlier one on the same asset",
    ),
    ("source_file", "string", "Source lineage"),
    ("ingestion_ts", "timestamp", "Source lineage"),
    ("batch_id", "string", "Source batch"),
    ("source_system", "string", "Source lineage"),
    ("source_system_key", "string", "source_system|work_order_id"),
    ("_ingestion_ts", "timestamp", "Bronze ingestion time of the kept row"),
    ("_batch_id", "string", "Bronze micro-batch of the kept row"),
    ("_source_file", "string", "Bronze source file of the kept row"),
    ("_source_system", "string", "Bronze source system of the kept row"),
    ("_record_hash", "string", "Bronze record hash, joins back to Bronze"),
    ("_silver_run_id", "string", "Run metadata"),
    ("_silver_processed_ts", "timestamp", "Run metadata"),
    ("_silver_row_hash", "string", "SHA-256 of business, fix-rule and flag columns"),
]
ROW_HASH_COLS = [
    n for n, _, _ in SILVER_SCHEMA if not n.startswith("_") and n not in LINEAGE_COLS
]

# Shared silver_dq_quarantine schema: identical in silver_maintenance.py and silver_sensor_event.py
# (spec: context/silver_max/silver_maintenance_rules.md 6.3). Change all three together.
QUARANTINE_DDL = """CREATE TABLE IF NOT EXISTS {table} (
    entity STRING, record_id STRING, rule_id STRING, quarantine_type STRING, field_name STRING,
    reason STRING, original_values STRING, record_version INT, _batch_id STRING,
    _source_file STRING, quarantine_ts TIMESTAMP, last_seen_ts TIMESTAMP, last_run_id STRING,
    resolution_status STRING, resolved_ts TIMESTAMP
) USING DELTA"""
AUDIT_SCHEMA = (
    "table_name string, rule_name string, dimension string, severity string, "
    "description string, records_evaluated long, records_failed long, "
    "failure_rate double, run_ts timestamp, run_id string"
)


class DataQualityError(Exception):
    """Raised when the Bronze input or an ERROR rule makes it unsafe to write Silver."""


def qualify_tables(catalog: str, schema: str) -> dict[str, str]:
    """Fully qualified table names; catalog and schema come from notebook widgets."""
    return {key: f"{catalog}.{schema}.{name}" for key, name in TABLE_NAMES.items()}


# ======== 2. clean_worker_ord: the cleaning tools =================================================
def _true(condition: Column) -> Column:
    """A null condition counts as False, so flags are never null."""
    return F.coalesce(condition, F.lit(False))


def _set_flags(df: DataFrame, conditions: dict[str, Column]) -> DataFrame:
    """Add one boolean _f_<RULE> column per rule."""
    return df.select(
        "*", *[_true(cond).alias(f"_f_{rule}") for rule, cond in conditions.items()]
    )


def _row_hash(columns: list[str]) -> Column:
    """SHA-256 over the columns, nulls replaced by a fixed marker."""
    parts = [F.coalesce(F.col(c).cast("string"), F.lit(NULL_MARKER)) for c in columns]
    return F.sha2(F.concat_ws("\x1f", *parts), 256)


def _lookup(column: Column, mapping: dict) -> Column:
    """CASE WHEN chain, null for unknown keys (a map lookup would fail under ANSI mode)."""
    expression = F.lit(None)
    for key, value in mapping.items():
        expression = F.when(column == key, F.lit(value)).otherwise(expression)
    return expression


def _fix_rule_column(labels: dict[str, str]) -> Column:
    """Names of the fixes applied to a row joined by semicolon, or UNCHANGED."""
    applied = [
        F.when(F.col(f"_f_{rule}"), F.lit(name)) for rule, name in labels.items()
    ]
    joined = F.concat_ws(";", *applied)
    return F.when(joined == "", F.lit("UNCHANGED")).otherwise(joined)


def _to_eur(amount: str) -> Column:
    """Local amount x fixed rate, HALF_UP to 2 places, decimal arithmetic."""
    return F.round(F.col(amount) * F.col("fx_rate_to_eur"), 2).cast("decimal(12,2)")


def _within_tolerance(
    actual: Column, expected: Column, absolute_tolerance: Decimal
) -> Column:
    """EUR rows must match to the cent, GBP/USD rows within NON_EUR_COST_TOLERANCE_PCT."""
    tolerance = F.when(F.col("currency") == "EUR", F.lit(absolute_tolerance)).otherwise(
        F.abs(expected) * (NON_EUR_COST_TOLERANCE_PCT / 100)
    )
    return F.abs(actual - expected) <= tolerance


def _downtime_set_count() -> Column:
    return reduce(add, [F.col(c).isNotNull().cast("int") for c in DOWNTIME_COLS])


def _is_running_downtime() -> Column:
    """In Progress work order whose downtime has started but not ended: valid."""
    return (
        (F.col("status") == "In Progress")
        & F.col("downtime_start_ts").isNotNull()
        & F.col("downtime_type").isNotNull()
        & F.col("downtime_end_ts").isNull()
        & F.col("downtime_minutes").isNull()
    )


def adapt_bronze_columns(df: DataFrame) -> DataFrame:
    """Rename Bronze audit columns (same map as maintenance history), copy _source_system."""
    df = df.select(*[F.col(c).alias(BRONZE_COLUMN_MAP.get(c, c)) for c in df.columns])
    if "source_system" in df.columns:
        df = df.withColumn("_source_system", F.col("source_system"))
    return df


def check_contracts(df: DataFrame) -> None:
    """SCHEMA_CONTRACT: every required column must be present."""
    missing = sorted(set(REQUIRED_COLS) - set(df.columns))
    if missing:
        raise DataQualityError(f"SCHEMA_CONTRACT: Bronze columns missing: {missing}")


def filter_loaded(df: DataFrame) -> DataFrame:
    """Keep rows with _load_status = SUCCESS, print the skipped count, stop on zero rows."""
    loaded = df.filter(F.trim(F.col("_load_status")) == BRONZE_LOADED_STATUS)
    total, kept = df.count(), loaded.count()
    print(
        f"Bronze rows read: {total:,}; skipped (load_status <> {BRONZE_LOADED_STATUS}): "
        f"{total - kept:,}"
    )
    if kept == 0:
        raise DataQualityError("No Bronze row with a loaded status: nothing to do.")
    return loaded


def standardise(df: DataFrame) -> DataFrame:
    """Trim, empty -> null, keep case, parse types without throwing (TYPE_CAST flags failures)."""
    dtypes = dict(df.dtypes)
    columns, cast_failures = [], []
    for name in REQUIRED_COLS:
        text = F.nullif(F.trim(F.col(name).cast("string")), F.lit(""))
        if name in TIMESTAMP_COLS:
            parsed = (
                F.col(name)
                if dtypes[name] == "timestamp"
                else F.try_to_timestamp(text, F.lit(TIMESTAMP_PATTERN))
            )
        elif name in NUMERIC_TYPES:
            sql_type = NUMERIC_TYPES[name]
            parsed = F.expr(
                f"try_cast(nullif(trim(cast(`{name}` as string)), '') as {sql_type})"
            )
        else:
            columns.append(text.alias(name))
            continue
        columns.append(parsed.alias(name))
        cast_failures.append(text.isNotNull() & parsed.isNull())
    return df.select(*columns, reduce(or_, cast_failures).alias("_f_TYPE_CAST"))


def deduplicate(df: DataFrame) -> DataFrame:
    """Flag exact duplicates and superseded versions. Drops nothing: the caller filters."""
    df = df.withColumn("_dup_hash", _row_hash(BUSINESS_COLS))
    first_copy = Window.partitionBy("_dup_hash").orderBy(
        "_ingestion_ts", "_source_file", "_record_hash"
    )
    df = df.withColumn("_f_DUP_EXACT", F.row_number().over(first_copy) > 1)
    # Never order by _ingestion_ts alone: both versions can share one ingestion time.
    version_order = Window.partitionBy(
        F.coalesce("work_order_id", "_record_hash")
    ).orderBy(
        F.col("_f_DUP_EXACT").asc(),
        F.col("record_version").desc(),
        F.col("last_modified_ts").desc(),
        F.col("_ingestion_ts").desc(),
        F.col("_record_hash").asc(),
    )
    is_older = F.row_number().over(version_order) > 1
    return df.withColumn("_f_VERSION_LATEST", ~F.col("_f_DUP_EXACT") & is_older)


def fix_timestamps(df: DataFrame) -> DataFrame:
    """closed_ts before created_ts: take last_modified_ts, but only when it passes every guard."""
    modified = F.col("last_modified_ts")
    closed_before_created = F.col("closed_ts") < F.col("created_ts")
    recoverable = _true(
        (F.col("record_version") == RECOVERY_RECORD_VERSION)
        & (modified >= F.col("created_ts"))
        & (F.col("actual_start_ts").isNull() | (modified >= F.col("actual_start_ts")))
        & (F.col("downtime_end_ts").isNull() | (modified >= F.col("downtime_end_ts")))
    )
    df = df.withColumn(
        "_orig_closed_ts", F.col("closed_ts")
    )  # kept for quarantine original_values
    df = _set_flags(
        df,
        {
            "TS_CLOSED_RECOVERED": closed_before_created & recoverable,
            "TS_CLOSED_UNRECOVERABLE": closed_before_created & ~recoverable,
        },
    )
    fixed_closed = (
        F.when(F.col("_f_TS_CLOSED_RECOVERED"), modified)
        .when(F.col("_f_TS_CLOSED_UNRECOVERABLE"), F.lit(None).cast("timestamp"))
        .otherwise(F.col("closed_ts"))
    )
    return df.withColumn("closed_ts", fixed_closed).withColumn(
        "ts_fix_rule", _fix_rule_column(TS_FIX_LABELS)
    )


def convert_currency(df: DataFrame) -> DataFrame:
    """Attach the fixed rate from FX_RATES_TO_EUR and convert both amounts to EUR."""
    rate = _lookup(F.col("currency"), FX_RATES_TO_EUR).cast("decimal(8,4)")
    df = df.withColumn("fx_rate_to_eur", rate)
    df = _set_flags(df, {"FX_UNAVAILABLE": F.col("fx_rate_to_eur").isNull()})
    return df.withColumn("labor_cost_eur", _to_eur("labor_cost")).withColumn(
        "parts_cost_eur", _to_eur("parts_cost")
    )


def fix_labour(df: DataFrame) -> DataFrame:
    """L1/L2/L3 repair labor_hours, LC1 repairs labor_cost, all from the row's own data."""
    df = df.withColumn(
        "_orig_labor_hours", F.col("labor_hours")
    )  # kept for quarantine original_values
    hours, cost, rate = (
        F.col("labor_hours"),
        F.col("labor_cost"),
        F.col("fx_rate_to_eur"),
    )
    standard = F.lit(STANDARD_LABOUR_RATE_EUR_H)
    hours_from_cost = F.round(cost * rate / standard, 2)
    is_sentinel, is_negative, cost_is_valid = (
        hours.isin(LABOUR_SENTINELS),
        hours < 0,
        cost > 0,
    )
    # Bad hours are found by sentinel list and cost consistency, never by a "> 24 h" threshold.
    df = _set_flags(
        df,
        {
            "L1": is_sentinel & cost_is_valid,
            "L2": is_negative
            & cost_is_valid
            & _within_tolerance(
                F.abs(hours), hours_from_cost, LABOUR_MATCH_TOLERANCE_H
            ),
        },
    )
    df = _set_flags(
        df, {"L3": (is_sentinel | is_negative) & ~F.col("_f_L1") & ~F.col("_f_L2")}
    )
    fixed_hours = (
        F.when(F.col("_f_L1"), hours_from_cost)
        .when(F.col("_f_L2"), F.abs(hours))
        .when(F.col("_f_L3"), F.lit(None))
        .otherwise(hours)
    )
    df = df.withColumn("labor_hours", fixed_hours.cast("decimal(8,2)"))
    df = _set_flags(df, {"LC1": (cost < 0) & (F.col("labor_hours") > 0)})
    cost_from_hours = F.round(F.col("labor_hours") * standard / rate, 2)
    fixed_cost = F.when(F.col("_f_LC1"), cost_from_hours).otherwise(cost)
    return (
        df.withColumn("labor_cost", fixed_cost.cast("decimal(12,2)"))
        .withColumn("labor_cost_eur", _to_eur("labor_cost"))  # LC1 may change the cost
        .withColumn("labor_fix_rule", _fix_rule_column(LABOUR_FIX_LABELS))
    )


def fix_parts(df: DataFrame) -> DataFrame:
    """P1: negative parts cost is nulled. No abs() and no +10: Gold recovers it from activities."""
    df = _set_flags(df, {"P1": F.col("parts_cost") < 0})
    nulled = F.when(F.col("_f_P1"), F.lit(None)).otherwise(F.col("parts_cost_eur"))
    return df.withColumn("parts_cost_eur", nulled.cast("decimal(12,2)")).withColumn(
        "parts_fix_rule", _fix_rule_column(PARTS_FIX_LABELS)
    )


def resolve_equipment(df: DataFrame) -> DataFrame:
    """Missing equipment stays null. Registration is not checked here: that is Gold."""
    df = _set_flags(df, {"EQ_MISSING": F.col("equipment_id").isNull()})
    resolution = F.when(F.col("_f_EQ_MISSING"), "MISSING").otherwise("SOURCE")
    return df.withColumn("equipment_resolution", resolution)


def _violates_domain(column: str, is_valid: Column) -> Column:
    """Value outside its domain. Empty values only count for columns that are required."""
    if column in CHECK_ONLY_WHEN_SET:
        return F.col(column).isNotNull() & ~is_valid
    return ~_true(is_valid)


def check_keys_and_codes(df: DataFrame) -> DataFrame:
    """Key presence, source key, code lists, ID formats, sensor trigger."""
    domain_checks = [(c, F.col(c).isin(values)) for c, values in CODES.items()] + [
        (c, F.col(c).rlike(pattern)) for c, pattern in PATTERNS.items()
    ]
    trigger = F.col("triggering_event_id")
    source_key = F.concat_ws("|", "source_system", "work_order_id")
    return _set_flags(
        df,
        {
            "MISSING_WORK_ORDER_ID": F.col("work_order_id").isNull(),
            "MISSING_CREATED_TS": F.col("created_ts").isNull(),
            "SOURCE_KEY_FORMAT": F.col("work_order_id").isNotNull()
            & ~F.col("source_system_key").eqNullSafe(source_key),
            "DOMAIN_CODES": reduce(
                or_, [_violates_domain(c, ok) for c, ok in domain_checks]
            ),
            "TRIGGER_FORMAT": trigger.isNotNull()
            & (~trigger.rlike(TRIGGER_PATTERN) | (F.col("wo_type") != "Predictive")),
        },
    )


def check_consistency(df: DataFrame) -> DataFrame:
    """Failure mode, root cause, SLA and cancellation rules (5.3)."""
    no_failure = F.col("failure_mode_code") == "NONE"
    expected_desc = _lookup(F.col("failure_mode_code"), FAILURE_MODE_DESC)
    sla_seconds = _lookup(
        F.col("priority"), {p: h * 3600 for p, h in SLA_HOURS.items()}
    )
    expected_due = F.unix_timestamp("created_ts") + sla_seconds
    root_cause_is_null = F.col("root_cause_code").isNull()
    has_reason = F.col("cancellation_reason").isNotNull()
    is_cancelled = F.col("status") == "Cancelled"
    return _set_flags(
        df,
        {
            "FM_DESC_MAP": ~F.col("failure_mode_desc").eqNullSafe(expected_desc),
            "FM_NONE_IFF_PLANNED_TYPE": ~no_failure.eqNullSafe(
                F.col("wo_type").isin(PLANNED_WO_TYPES)
            ),
            "RC_NULL_IFF_NOT_FAILURE_CLOSED": ~root_cause_is_null.eqNullSafe(
                no_failure | (F.col("status") != "Closed")
            ),
            "DUE_MATCHES_PRIORITY": F.unix_timestamp("due_ts") != expected_due,
            "CANCEL_REASON_IFF_CANCELLED": ~has_reason.eqNullSafe(is_cancelled),
        },
    )


def check_timestamps(df: DataFrame) -> DataFrame:
    """Checks that run after the closure fix (5.5)."""
    closed, created, start = (
        F.col("closed_ts"),
        F.col("created_ts"),
        F.col("actual_start_ts"),
    )
    after_extract = reduce(
        or_, [F.col(c) > F.col("ingestion_ts") for c in EVENT_TS_COLS]
    )
    return _set_flags(
        df,
        {
            "TS_ORDER": (closed < created)
            | (closed < start)
            | (F.col("downtime_end_ts") > closed),
            "TS_MODIFIED_BEFORE_CLOSE": F.col("last_modified_ts") < closed,
            "TS_AFTER_EXTRACT": after_extract,
            "START_BEFORE_CREATED": start < created,  # a warning, not a defect
        },
    )


def check_downtime(df: DataFrame) -> DataFrame:
    """Downtime checks and the overlap flag. Overlaps are flagged, never quarantined (5.9)."""
    start, end = F.col("downtime_start_ts"), F.col("downtime_end_ts")
    dt_type = F.col("downtime_type")
    minutes_calc = F.round((F.unix_timestamp(end) - F.unix_timestamp(start)) / 60)
    minutes_before_creation = (
        F.unix_timestamp("created_ts") - F.unix_timestamp(start)
    ) / 60
    # Running maximum of earlier end times per asset, not only the previous row.
    earlier = (
        Window.partitionBy("equipment_id")
        .orderBy("downtime_start_ts", "work_order_id")
        .rowsBetween(Window.unboundedPreceding, -1)
    )
    type_mismatch = (dt_type == "unplanned") != F.col("wo_type").isin(
        UNPLANNED_WO_TYPES
    )
    df = _set_flags(
        df,
        {
            "DT_COMPLETE": ~(_downtime_set_count().isin(0, 4) | _is_running_downtime()),
            "DT_MINUTES_MATCH": F.abs(F.col("downtime_minutes") - minutes_calc) > 1,
            "DT_TYPE_MATCHES_WO_TYPE": dt_type.isNotNull() & type_mismatch,
            "DT_TIMING": (
                (dt_type == "unplanned") & ~minutes_before_creation.between(0, 30)
            )
            | ((dt_type == "planned") & (start < F.col("created_ts"))),
            "DT_OVERLAP": F.col("equipment_id").isNotNull()
            & (start < F.max(end).over(earlier)),
        },
    )
    return df.withColumn("dq_downtime_overlap", F.col("_f_DT_OVERLAP"))


def check_labour_consistency(df: DataFrame) -> DataFrame:
    """Every Closed row: labor_cost = hours x standard rate / fixed rate (within tolerance)."""
    standard = F.lit(STANDARD_LABOUR_RATE_EUR_H)
    expected = F.round(F.col("labor_hours") * standard / F.col("fx_rate_to_eur"), 2)
    both_set = F.col("labor_hours").isNotNull() & F.col("labor_cost").isNotNull()
    consistent = _within_tolerance(
        F.col("labor_cost"), expected, COST_MATCH_TOLERANCE_EUR
    )
    is_closed = F.col("status") == "Closed"
    return _set_flags(df, {"LABOR_COST_CONSISTENT": is_closed & both_set & ~consistent})


def check_status_lifecycle(df: DataFrame) -> DataFrame:
    """Strict field pattern per status. Nulls are valid and never filled with placeholders."""
    status, started = F.col("status"), F.col("actual_start_ts").isNotNull()
    has_reason = F.col("cancellation_reason").isNotNull()
    has_closed_ts = F.col("closed_ts").isNotNull()
    finished = has_closed_ts | F.col("_f_TS_CLOSED_UNRECOVERABLE")
    has_hours = F.col("labor_hours").isNotNull()
    has_money = (
        F.col("labor_cost").isNotNull()
        & F.col("parts_cost").isNotNull()
        & (has_hours | F.col("_f_L3"))
    )
    no_money = ~(
        has_hours | F.col("labor_cost").isNotNull() | F.col("parts_cost").isNotNull()
    )
    no_downtime = _downtime_set_count() == 0
    is_valid = (
        F.when(
            status == "Open",
            ~started & ~finished & no_money & no_downtime & ~has_reason,
        )
        # In Progress: downtime may be none, running or already finished (DT_COMPLETE
        # checks its fields are all-or-none); section 5.3 only listed none/running
        .when(
            status == "In Progress", started & ~has_closed_ts & no_money & ~has_reason
        )
        .when(status == "Closed", started & finished & has_money & ~has_reason)
        .when(
            status == "Cancelled",
            ~started & finished & no_money & no_downtime & has_reason,
        )
        .otherwise(True)
    )  # unknown status is DOMAIN_CODES' job
    return _set_flags(df, {"STATUS_LIFECYCLE": ~is_valid})


def check_rules(df: DataFrame) -> DataFrame:
    """Equipment resolution plus every check that only flags."""
    df = resolve_equipment(df)
    checks = (
        check_keys_and_codes,
        check_consistency,
        check_timestamps,
        check_downtime,
        check_labour_consistency,
        check_status_lifecycle,
    )
    return reduce(lambda acc, check: check(acc), checks, df)


def clean_worker_ord(bronze: DataFrame) -> tuple[DataFrame, DataFrame]:
    """Bronze rows -> cleaned work orders: latest version, fixes applied, one _f_<RULE> per rule.

    Returns:
        (cleaned, dedup_flags): `cleaned` holds the deduplicated rows. `dedup_flags` holds every
        loaded Bronze row with the DUP_EXACT / VERSION_LATEST flags, for the row flow.
    """
    df = adapt_bronze_columns(bronze)
    check_contracts(df)
    flagged = deduplicate(standardise(filter_loaded(df)))
    kept = flagged.filter(~F.col("_f_DUP_EXACT") & ~F.col("_f_VERSION_LATEST"))
    steps = (fix_timestamps, convert_currency, fix_labour, fix_parts, check_rules)
    return reduce(lambda acc, step: step(acc), steps, kept), flagged


# ======== 3. creating_silver_table: counts, error gate, writes ====================================
Stats = dict[
    str, dict[str, int]
]  # source batch (or "ALL") -> row counts and failed counts


def add_row_quarantine(df: DataFrame) -> DataFrame:
    """_is_row_quarantined: the row never reaches Silver (0 expected today)."""
    flags = [F.col(f"_f_{rule}") for rule in ROW_QUARANTINE_RULES]
    return df.withColumn("_is_row_quarantined", reduce(or_, flags))


def collect_stats(cleaned: DataFrame, dedup_flags: DataFrame) -> Stats:
    """Row counts and failed counts per rule, per source batch plus 'ALL'."""

    def failed(rule: str) -> Column:
        return F.coalesce(F.sum(F.col(f"_f_{rule}").cast("int")), F.lit(0)).alias(rule)

    all_label = F.coalesce(
        "batch_id", F.lit("ALL")
    )  # rollup marks the total row with null
    quarantined = F.col("_is_row_quarantined").cast("int")
    after = (
        cleaned.rollup("batch_id")
        .agg(
            F.count("*").alias("after_dedup"),
            F.coalesce(F.sum(quarantined), F.lit(0)).alias("row_quarantine"),
            *[failed(r) for r in RULES if r not in DEDUP_RULES],
        )
        .withColumn("batch_id", all_label)
    )
    before = (
        dedup_flags.rollup("batch_id")
        .agg(F.count("*").alias("bronze"), *[failed(r) for r in DEDUP_RULES])
        .withColumn("batch_id", all_label)
    )
    rows = after.join(before, "batch_id", "outer").fillna(0).collect()
    return {row["batch_id"]: row.asDict() for row in rows}


def write_dq_results(
    spark: SparkSession, stats: Stats, table: str, run_id: str
) -> None:
    """Append one row per rule for this run to the shared, append-only dq_results."""
    total, run_ts = stats["ALL"], datetime.now(timezone.utc)
    rows = []
    for rule, (dimension, severity, description) in RULES.items():
        evaluated = total["bronze"] if rule in DEDUP_RULES else total["after_dedup"]
        rate = total[rule] / evaluated if evaluated else 0.0
        rows.append(
            (
                TABLE_NAMES["silver"],
                rule,
                dimension,
                severity,
                description,
                evaluated,
                total[rule],
                rate,
                run_ts,
                run_id,
            )
        )
    spark.createDataFrame(rows, AUDIT_SCHEMA).write.mode("append").saveAsTable(table)


def raise_on_errors(stats: Stats) -> None:
    """Error gate: any ERROR rule above 0 stops the run before Silver or quarantine is touched."""
    firing = {
        r: stats["ALL"][r]
        for r, (_, severity, _) in RULES.items()
        if severity == "ERROR" and stats["ALL"][r] > 0
    }
    if firing:
        detail = ", ".join(f"{rule}={count:,}" for rule, count in firing.items())
        raise DataQualityError(
            f"Error rules fired, Silver and quarantine untouched: {detail}"
        )


def build_silver(df: DataFrame, run_id: str) -> DataFrame:
    """Rows that survive, cast to SILVER_SCHEMA, with run metadata and the row hash."""
    kept = (
        df.filter(~F.col("_is_row_quarantined"))
        .withColumn("_silver_run_id", F.lit(run_id))
        .withColumn("_silver_processed_ts", F.current_timestamp())
    )
    typed = kept.select(
        *[
            F.col(name).cast(dtype).alias(name)
            for name, dtype, _ in SILVER_SCHEMA
            if name != "_silver_row_hash"
        ]
    )
    return typed.withColumn("_silver_row_hash", _row_hash(ROW_HASH_COLS)).select(
        *[name for name, _, _ in SILVER_SCHEMA]
    )


def build_quarantine(df: DataFrame, run_id: str) -> DataFrame:
    """One OPEN record per flagged row and field. Originals stay in Bronze (join on record_hash)."""

    def record(rule: str, kind: str, field: str | None, original: Column) -> DataFrame:
        return df.filter(F.col(f"_f_{rule}")).select(
            F.lit(QUARANTINE_ENTITY).alias("entity"),
            F.coalesce("work_order_id", "_record_hash").alias("record_id"),
            F.lit(rule).alias("rule_id"),
            F.lit(kind).alias("quarantine_type"),
            F.lit(field).cast("string").alias("field_name"),
            F.lit(RULES[rule][2]).alias("reason"),
            original.alias("original_values"),
            F.col("record_version"),
            F.col("_batch_id"),
            F.col("_source_file"),
            F.current_timestamp().alias("quarantine_ts"),
            F.current_timestamp().alias("last_seen_ts"),
            F.lit(run_id).alias("last_run_id"),
            F.lit("OPEN").alias("resolution_status"),
            F.lit(None).cast("timestamp").alias("resolved_ts"),
        )

    parts = []
    for rule, (kind, fields) in QUARANTINE_RULES.items():
        if kind == "ROW":
            parts.append(record(rule, kind, None, F.to_json(F.struct(*BUSINESS_COLS))))
        for field, source in fields:
            value = F.col(source).alias(source.removeprefix("_orig_"))
            parts.append(record(rule, kind, field, F.to_json(F.struct(value))))
    return reduce(DataFrame.unionByName, parts)


def create_tables(spark: SparkSession, tables: dict[str, str]) -> None:
    """Create Silver (with column comments, change data feed) and quarantine when missing."""
    columns = ", ".join(f"{n} {t} COMMENT '{c}'" for n, t, c in SILVER_SCHEMA)
    silver = tables["silver"]
    spark.sql(
        f"CREATE TABLE IF NOT EXISTS {silver} ({columns}) USING DELTA "
        "TBLPROPERTIES (delta.enableChangeDataFeed = true)"
    )
    spark.sql(QUARANTINE_DDL.format(table=tables["quarantine"]))


def merge_silver(spark: SparkSession, silver: DataFrame, target: str) -> None:
    """Idempotent MERGE on work_order_id: a rerun on unchanged data changes 0 rows."""
    silver.createOrReplaceTempView("_silver_updates")
    result = spark.sql(f"""
        MERGE INTO {target} t USING _silver_updates s ON t.work_order_id = s.work_order_id
        WHEN MATCHED AND s.record_version > t.record_version THEN UPDATE SET *
        WHEN MATCHED AND s.record_version = t.record_version
             AND s._silver_row_hash <> t._silver_row_hash THEN UPDATE SET *
        WHEN NOT MATCHED THEN INSERT *
        WHEN NOT MATCHED BY SOURCE THEN DELETE""").first()
    print(
        f"Silver MERGE: inserted {result['num_inserted_rows']:,}, "
        f"updated {result['num_updated_rows']:,}, deleted {result['num_deleted_rows']:,}"
    )


def merge_quarantine(spark: SparkSession, quarantine: DataFrame, target: str) -> None:
    """MERGE, never append. Key includes field_name (FX_UNAVAILABLE writes one per field)."""
    quarantine.createOrReplaceTempView("_quarantine_updates")
    spark.sql(f"""
        MERGE INTO {target} t USING _quarantine_updates s
        ON t.entity = s.entity AND t.record_id = s.record_id
           AND t.rule_id = s.rule_id AND t.field_name <=> s.field_name
        WHEN MATCHED THEN UPDATE SET last_seen_ts = s.last_seen_ts, last_run_id = s.last_run_id,
             resolution_status = 'OPEN', resolved_ts = NULL
        WHEN NOT MATCHED THEN INSERT *
        WHEN NOT MATCHED BY SOURCE AND t.entity = '{QUARANTINE_ENTITY}'
             AND t.resolution_status = 'OPEN'
             THEN UPDATE SET resolution_status = 'RESOLVED', resolved_ts = current_timestamp()""")


def print_report(stats: Stats) -> None:
    """Print the row flow (every Bronze row has one outcome) and the rules that fired."""
    batches = sorted(b for b in stats if b != "ALL") + ["ALL"]
    flow = [
        ("Bronze (loaded)", "bronze"),
        ("- exact duplicates", "DUP_EXACT"),
        ("- superseded versions", "VERSION_LATEST"),
        ("= after dedup", "after_dedup"),
        ("- row quarantine", "row_quarantine"),
    ]

    def show(label: str, values: list[int], extra: str = "") -> None:
        print(f"{label:<34}{extra:<18}" + "".join(f"{v:>21,}" for v in values))

    print(f"\n{'Row flow':<52}" + "".join(f"{b:>21}" for b in batches))
    for label, key in flow:
        show(label, [stats[b][key] for b in batches])
    show(
        "= Silver",
        [stats[b]["after_dedup"] - stats[b]["row_quarantine"] for b in batches],
    )
    print(f"\n{'Rules that fired':<52}")
    for rule, (_, severity, _) in RULES.items():
        if stats["ALL"][rule]:
            show(rule, [stats[b][rule] for b in batches], severity)


def creating_silver_table(
    spark: SparkSession,
    cleaned: DataFrame,
    dedup_flags: DataFrame,
    tables: dict[str, str],
    run_id: str,
) -> None:
    """Count rules, log them, apply the error gate, then MERGE Silver and quarantine."""
    cleaned = add_row_quarantine(cleaned)
    stats = collect_stats(cleaned, dedup_flags)
    write_dq_results(
        spark, stats, tables["audit"], run_id
    )  # also written when the gate stops
    print_report(stats)
    raise_on_errors(stats)
    create_tables(spark, tables)
    merge_silver(spark, build_silver(cleaned, run_id), tables["silver"])
    merge_quarantine(spark, build_quarantine(cleaned, run_id), tables["quarantine"])


# ======== 4. main =================================================================================
def main() -> None:
    """Read the widgets, clean Bronze, write Silver, quarantine and DQ results."""
    spark = SparkSession.builder.getOrCreate()
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    dbutils = globals()["dbutils"]  # injected by the notebook runtime
    dbutils.widgets.text("catalog", "data2026acc123456")
    dbutils.widgets.text("schema", "team3")
    catalog, schema = dbutils.widgets.get("catalog"), dbutils.widgets.get("schema")
    if not catalog or not schema:
        raise ValueError("Fill the 'catalog' and 'schema' widgets.")
    tables = qualify_tables(catalog, schema)
    cleaned, dedup_flags = clean_worker_ord(spark.table(tables["bronze"]))
    creating_silver_table(spark, cleaned, dedup_flags, tables, run_id=str(uuid.uuid4()))
    print(f"\nDone. FX source: {FX_RATE_SOURCE}.")


if __name__ == "__main__":
    main()
