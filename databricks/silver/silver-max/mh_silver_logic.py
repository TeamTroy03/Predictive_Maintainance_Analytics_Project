# Databricks notebook source
# MAGIC %md
# MAGIC # Silver: `maintenance_history` (Bronze → Silver)
# MAGIC
# MAGIC Cleans `bronze_maintenance_history` into `silver_maintenance_history`
# MAGIC (rules: `silver_maintenance_history_rules.md`, § = section there). Silver holds the
# MAGIC cleaned columns and the evidence of the cleaning only. Costs, categories, lifecycle
# MAGIC checks and work-order reconciliation belong in Gold.
# MAGIC
# MAGIC **Inputs.** All Bronze maintenance rows (rebuilt every run, §6.1). Two references:
# MAGIC Silver work orders (equipment, failure mode, root cause, labour hours and parts cost per
# MAGIC work order) and the list of `equipment_id`s in Silver equipment (EQ_UNREGISTERED).
# MAGIC
# MAGIC **Flow.** standardise → deduplicate → clean (equipment, timestamps, parts, labour) →
# MAGIC checks → error gate → MERGE Silver and quarantine, append rule counts and row flow.
# MAGIC
# MAGIC **Adding a rule.** Add it to `RULES`, then set a boolean `_f_<RULE>` column in a step.
# MAGIC Counting, the error gate and the DQ results pick it up from there.
# MAGIC
# MAGIC `test_mh_silver_logic.py` imports this file. Edit here, run the tests, paste into the notebook.

# COMMAND ----------

import json
import uuid
from datetime import UTC, datetime
from functools import reduce

from pydantic import BaseModel, ConfigDict
from pyspark.sql import Column, DataFrame, SparkSession, Window
from pyspark.sql import functions as F
from pyspark.sql import types as T

ENTITY = "maintenance_history"

CONFIG = {
    "bronze_loaded_status": "SUCCESS",
    # Bronze notebook column -> name used here (§2.2). Bronze keeps the source `batch_id` as
    # `source_batch_id` and uses `batch_id` for its own micro-batch number.
    "bronze_column_map": {"batch_id": "_batch_id", "source_batch_id": "batch_id",
                          "ingestion_timestamp": "_ingestion_ts", "source_filename": "_source_file",
                          "record_hash": "_record_hash", "load_status": "_load_status"},
    "timestamp_pattern": "yyyy-MM-dd'T'HH:mm:ss'Z'",
    "date_pattern": "yyyy-MM-dd",
    "legacy_source_system": "LEGACY_CMMS",
    # C1 domain assumption: these activity types never use parts.
    "no_parts_activity_types": ["Inspect", "Align", "Adjust", "Calibrate"],
    "labour_sentinels": [999],
    "min_booking_h": "0.25",
    "allowed_factors": [1, 2],  # L2 domain assumption: 1 or 2 people per activity
    "factor_tolerance": "0.05",
    "max_activities_per_work_order": 2,
    "codes": {
        "activity_type": ["Inspect", "Replace", "Align", "Clean", "Service", "Calibrate",
                          "Lubricate", "Repair", "Adjust"],
        "failure_mode_code": ["NONE", "BRG-WEAR", "MISALIGN", "IMBAL", "OVERHEAT", "LUB-FAIL",
                              "WIND-INS", "SEAL-LEAK", "CAVIT", "VALVE-FAIL", "GEAR-WEAR", "CLOG"],
        "root_cause_code": ["RC-WEAR", "RC-CONTAM", "RC-LUB", "RC-INST", "RC-OPER", "RC-MAINT",
                            "RC-DESIGN", "RC-MATL"],
        "detection_method": ["scheduled_pm", "inspection", "operator_report",
                             "condition_monitoring", "alarm"],
        "outcome": ["Resolved", "Temporary fix"],
    },
    "technician_pattern": "^TECH-[0-9]{2}$",
    "component_to_code": {
        "Whole asset": "GENERAL", "Shaft coupling": "COUPLING", "Drive-end bearing": "BRG-DE",
        "Bearing and coupling": "BRG-DE", "Non-drive-end bearing": "BRG-NDE",
        "Cooling system / heat exchanger": "COOLING-SYS", "Lubrication system": "LUBE-SYS",
        "Sensor package": "SENSOR-PKG", "Oil filter": "OIL-FILTER", "Rotor assembly": "ROTOR",
        "Stator winding": "STATOR-WDG", "Mechanical seal": "SEAL-MECH", "Impeller": "IMPELLER",
        "Valve assembly": "VALVE", "Gear set": "GEAR-SET", "Suction strainer / filter": "STRAINER",
        "Fan blade set": "FAN-BLADE",
    },
    "tables": {"work_orders": "silver_work_orders", "equipment": "silver_equipment",
               "silver": "silver_maintenance_history",
               "quarantine": "silver_dq_quarantine", "audit": "dq_results",
               "reconciliation": "silver_dq_reconciliation"},
}

SOURCE_COLUMNS = [
    "maintenance_id", "work_order_id", "equipment_id", "activity_start_ts", "activity_end_ts",
    "activity_type", "component", "component_code", "failure_mode_code", "root_cause_code",
    "detection_method", "action_description", "parts_qty", "parts_cost_eur", "labor_hours",
    "technician_id", "outcome", "next_due_date", "last_modified_ts", "record_version",
    "source_file", "ingestion_ts", "batch_id", "source_system", "source_system_key",
]
LINEAGE_COLUMNS = ["source_file", "ingestion_ts", "batch_id", "source_system", "source_system_key"]
BUSINESS_COLUMNS = [c for c in SOURCE_COLUMNS if c not in LINEAGE_COLUMNS]
BRONZE_AUDIT_COLUMNS = ["_ingestion_ts", "_batch_id", "_source_file", "_source_system",
                        "_record_hash", "_load_status"]
# What this notebook looks up per work order. Invalid values must arrive as null (§3.1).
WORK_ORDER_COLUMNS = ["work_order_id", "equipment_id", "failure_mode_code", "root_cause_code",
                      "labor_hours", "parts_cost_eur"]

ERROR, DROP, FIX, FLAG, WARN = "ERROR", "DROP", "FIX", "FLAG", "WARN"
FIELD_Q, ROW_Q = "FIELD_QUARANTINE", "ROW_QUARANTINE"

# rule_id: (severity, DQ dimension, description), in report order (§7). ERROR stops the run.
RULES = {
    "TYPE_CAST": (ERROR, "validity", "Non-empty value that doesn't parse to its type"),
    "DUP_EXACT": (DROP, "uniqueness", "Exact duplicate of the business columns"),
    "VERSION_LATEST": (DROP, "uniqueness", "Older record_version superseded by a newer one"),
    "SOURCE_KEY_FORMAT": (ERROR, "validity", "source_system_key = source_system|maintenance_id"),
    "DOMAIN_CODES": (ERROR, "validity", "Codes in their lists; technician_id matches TECH-nn"),
    "COMPONENT_CODE_MAP": (ERROR, "consistency", "component maps to component_code"),
    "WO_NULL_NON_LEGACY": (ERROR, "completeness", "Null work_order_id on a non-legacy row"),
    "WO_ORPHAN": (WARN, "referential_integrity", "work_order_id not in the work orders"),
    "RC_NULL_IFF_NONE": (ERROR, "consistency", "Root cause null exactly when failure mode NONE"),
    "NDD_IFF_PLANNED": (ERROR, "consistency", "next_due_date set exactly when failure mode NONE"),
    "EQ_MATCHES_WO": (ERROR, "consistency", "equipment_id differs from the work order's"),
    "EQ_RECOVERED_FROM_WO": (FIX, "completeness", "Missing equipment_id taken from the work order"),
    "EQ_MISSING_UNRECOVERABLE": (ROW_Q, "completeness", "Missing equipment_id, no work order"),
    "EQ_UNREGISTERED": (FLAG, "referential_integrity", "equipment_id not in the equipment master"),
    "FM_MATCHES_WO": (ERROR, "consistency", "failure_mode_code differs from the work order's"),
    "RC_CONFLICT_WITH_WO": (WARN, "consistency", "root_cause_code differs from the work order's"),
    "ACTIVITIES_PER_WO": (WARN, "consistency", "More than 2 activities on one work order"),
    "TS_END_BEFORE_START": (FIELD_Q, "validity", "activity_end_ts before start; end nulled"),
    "TS_AFTER_EXTRACT": (WARN, "validity", "Activity start or end after the extract's ingestion_ts"),
    "TS_MODIFIED_BEFORE_END": (WARN, "consistency", "last_modified_ts before activity_end_ts"),
    "NDD_AFTER_END": (WARN, "consistency", "next_due_date not after the activity end date"),
    "C1": (FIX, "validity", "Non-zero parts cost on a no-parts activity type zeroed"),
    "C2": (FIX, "validity", "Negative parts cost recovered from the work order"),
    "C3": (FIELD_Q, "validity", "Negative parts cost not recoverable; set to null"),
    "Q1": (FIX, "consistency", "Quantity zeroed where the cleaned cost is 0"),
    "Q2": (FIX, "validity", "Negative parts quantity with a real cost made positive"),
    "PARTS_QTY_COST_CONSISTENT": (ERROR, "consistency", "parts_qty is 0 exactly when cost is 0"),
    "L1": (FIX, "validity", "Labour sentinel value (999) nulled for recovery"),
    "L2": (FIX, "validity", "Labour booked in minutes converted to hours"),
    "L3": (FIX, "validity", "Negative labour hours made positive"),
    "L4_RECOVERED": (FIX, "completeness", "Labour sentinel recovered from the work order"),
    "L4_UNRECOVERABLE": (FIELD_Q, "completeness", "Labour sentinel not recoverable; set to null"),
    "LABOR_UNEXPLAINED_RATIO": (ERROR, "validity", "Labour/duration not factor 1 or 2 after fixes"),
}
RAW_RULES = ["TYPE_CAST", "DUP_EXACT", "VERSION_LATEST"]  # counted on all Bronze rows

# Rules that write quarantine records (§6.3): rule -> (field set to null, or None = whole row).
QUARANTINE_FIELDS = {"EQ_MISSING_UNRECOVERABLE": None, "TS_END_BEFORE_START": "activity_end_ts",
                     "C3": "parts_cost_eur", "L4_UNRECOVERABLE": "labor_hours"}

# Values written to the *_fix_rule columns (§5.6, §5.7).
PARTS_FIX_LABELS = {"C1": "NO_PARTS_ACTIVITY_COST_ZEROED", "C2": "NEG_COST_RECOVERED_FROM_WO",
                    "C3": "NEG_COST_UNRECOVERABLE", "Q1": "ZERO_COST_QTY_ZEROED",
                    "Q2": "NEG_QTY_CORRECTED"}
LABOR_FIX_LABELS = {"L2": "MINUTES_TO_HOURS", "L3": "NEGATIVE_ABS",
                    "L4_RECOVERED": "SENTINEL_999_RECOVERED",
                    "L4_UNRECOVERABLE": "LABOR_HOURS_UNRECOVERABLE"}

# Silver table (§9): (column, type, comment), in table order.
SILVER_SCHEMA = [
    ("maintenance_id", "string", "Activity ID MH-nnnnn"),
    ("work_order_id", "string", "Work order the activity ran under; null only for legacy"),
    ("equipment_id", "string", "Asset worked on; recovered from the work order when missing"),
    ("activity_start_ts", "timestamp", "Work started, UTC"),
    ("activity_end_ts", "timestamp", "Work finished, UTC. Null after TS_END_BEFORE_START"),
    ("activity_type", "string", "Inspect, Replace, Align, Clean, Service, Calibrate, ..."),
    ("component", "string", "Component worked on"),
    ("component_code", "string", "Component code"),
    ("failure_mode_code", "string", "Failure addressed; NONE = planned work"),
    ("root_cause_code", "string", "Root cause. Null = planned work (valid)"),
    ("detection_method", "string", "How the need was found"),
    ("action_description", "string", "Action text"),
    ("parts_qty", "int", "Spare parts used, >= 0"),
    ("parts_cost_eur", "decimal(12,2)", "Spare-parts cost, EUR. Null only after C3"),
    ("labor_hours", "decimal(8,2)", "Booked person-hours. Null only after L4 failure"),
    ("technician_id", "string", "TECH-nn. Pseudonymous"),
    ("outcome", "string", "Resolved or Temporary fix"),
    ("next_due_date", "date", "Next planned occurrence. Null = failure work (valid)"),
    ("last_modified_ts", "timestamp", "Last change in the source system, UTC"),
    ("record_version", "int", "Version kept"),
    ("parts_fix_rule", "string", "Parts rules applied, ;-joined, or UNCHANGED"),
    ("labor_fix_rule", "string", "Labour rules applied, ;-joined, or UNCHANGED"),
    ("equipment_resolution", "string", "SOURCE, RECOVERED_FROM_WO or UNREGISTERED"),
    ("dq_unknown_equipment", "boolean", "Equipment not in the equipment master"),
    ("dq_rc_conflict_with_wo", "boolean", "Root cause differs from the work order's"),
    ("source_file", "string", "Source lineage: extract file name"),
    ("ingestion_ts", "timestamp", "Source lineage: extract timestamp, UTC"),
    ("batch_id", "string", "Source lineage: source batch"),
    ("source_system", "string", "Source lineage: CMMS or LEGACY_CMMS"),
    ("source_system_key", "string", "Source lineage: source_system|maintenance_id"),
    ("_ingestion_ts", "timestamp", "Bronze lineage: load timestamp"),
    ("_batch_id", "string", "Bronze lineage: batch"),
    ("_source_file", "string", "Bronze lineage: file"),
    ("_source_system", "string", "Bronze lineage: source system"),
    ("_record_hash", "string", "Bronze lineage: row hash (join key back to Bronze, 6.5)"),
    ("_silver_run_id", "string", "Run that last wrote the row"),
    ("_silver_processed_ts", "timestamp", "When the row was last written"),
    ("_silver_row_hash", "string", "SHA-256 of business, fix-rule and flag columns"),
]
SILVER_COLUMNS = [name for name, _, _ in SILVER_SCHEMA]
# The row hash covers business, fix-rule and flag columns; never lineage or run metadata.
ROW_HASH_COLUMNS = SILVER_COLUMNS[: SILVER_COLUMNS.index("dq_rc_conflict_with_wo") + 1]

UNCHANGED = "UNCHANGED"
NULL_MARKER, HASH_SEPARATOR = "␀", "␟"  # null and "" hash differently

# COMMAND ----------
# MAGIC %md
# MAGIC ## Helpers

# COMMAND ----------


def _flag(condition: Column) -> Column:
    """A rule flag is never null: unknown counts as 'did not fire'."""
    return F.coalesce(condition, F.lit(False))


def _dec(value, sql_type: str) -> Column:
    """Decimal literal; money and hours are never double."""
    return F.lit(str(value)).cast(sql_type)


def _sha256(columns: list[str]) -> Column:
    parts = [F.coalesce(F.col(c).cast("string"), F.lit(NULL_MARKER)) for c in columns]
    return F.sha2(F.concat_ws(HASH_SEPARATOR, *parts), 256)


def _parse(df: DataFrame, c: str, sql_type: str, pattern: str | None = None) -> Column:
    """Parse without throwing: null when the value doesn't parse. Accepts string or typed input."""
    if pattern and isinstance(df.schema[c].dataType, T.StringType):
        ts = F.call_function("try_to_timestamp", F.col(c), F.lit(pattern))
        return F.to_date(ts) if sql_type == "date" else ts
    return F.expr(f"try_cast(`{c}` AS {sql_type})")


def _applied(labels: dict[str, str]) -> Column:
    """`;`-joined labels of the rules that fired on the row, or UNCHANGED."""
    joined = F.concat_ws(";", *[F.when(F.col(f"_f_{r}"), label) for r, label in labels.items()])
    return F.when(joined == "", UNCHANGED).otherwise(joined)


def _per_work_order(c: Column) -> Column:
    return c.over(Window.partitionBy("work_order_id"))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Read and standardise (§2.2, §5.1, §5.2)

# COMMAND ----------


def adapt_bronze_columns(df: DataFrame, cfg: dict = CONFIG) -> DataFrame:
    """Rename the Bronze notebook's columns to the names used here (one select, so the
    `batch_id` / `source_batch_id` swap can't collide)."""
    mapping = cfg["bronze_column_map"]
    df = df.select(*[F.col(f"`{c}`").alias(mapping.get(c, c)) for c in df.columns])
    if "_source_system" not in df.columns and "source_system" in df.columns:
        df = df.withColumn("_source_system", F.col("source_system"))
    return df


def check_contracts(bronze_df: DataFrame, work_orders_df: DataFrame,
                    equipment_df: DataFrame) -> None:
    """SCHEMA_CONTRACT: stop with every missing column listed."""
    missing = {"bronze": [c for c in SOURCE_COLUMNS + BRONZE_AUDIT_COLUMNS
                          if c not in bronze_df.columns],
               "work_orders": [c for c in WORK_ORDER_COLUMNS if c not in work_orders_df.columns],
               "equipment": [c for c in ["equipment_id"] if c not in equipment_df.columns]}
    problems = [f"{name} is missing {', '.join(cols)}" for name, cols in missing.items() if cols]
    if problems:
        raise ValueError("SCHEMA_CONTRACT failed: " + "; ".join(problems))


def standardise(df: DataFrame, cfg: dict = CONFIG) -> DataFrame:
    """Trim strings, empty → null, cast to Silver types (case never changes). TYPE_CAST flags
    a value that was there but doesn't parse. `_w_source_json` keeps the delivered row for
    quarantine records only."""
    df = df.withColumn("_w_source_json", F.to_json(F.struct(*SOURCE_COLUMNS)))
    strings = [c for c in SOURCE_COLUMNS if isinstance(df.schema[c].dataType, T.StringType)]
    df = df.withColumns({c: F.when(F.trim(c) != "", F.trim(c)) for c in strings})
    ts = cfg["timestamp_pattern"]
    parsed = {
        **{c: _parse(df, c, "timestamp", ts) for c in
           ["activity_start_ts", "activity_end_ts", "last_modified_ts", "ingestion_ts"]},
        "next_due_date": _parse(df, "next_due_date", "date", cfg["date_pattern"]),
        "parts_cost_eur": _parse(df, "parts_cost_eur", "decimal(12,2)"),
        "labor_hours": _parse(df, "labor_hours", "decimal(8,2)"),
        "parts_qty": _parse(df, "parts_qty", "int"),
        "record_version": _parse(df, "record_version", "int"),
    }
    failed = reduce(lambda a, b: a | b, [F.col(c).isNotNull() & e.isNull()
                                         for c, e in parsed.items()])
    return df.withColumns({**parsed, "_ingestion_ts": _parse(df, "_ingestion_ts", "timestamp"),
                           "_f_TYPE_CAST": _flag(failed)})


def deduplicate(df: DataFrame) -> DataFrame:
    """Flag (don't drop) DUP_EXACT and VERSION_LATEST.

    DUP_EXACT: same hash over the business columns (lineage excluded, so a row re-sent in a
    later batch is caught too); the earliest load is kept. VERSION_LATEST: the highest
    record_version per maintenance_id wins (then last_modified_ts, then latest load).
    """
    df = df.withColumn("_w_business_hash", _sha256(BUSINESS_COLUMNS))
    first_copy = Window.partitionBy("_w_business_hash").orderBy(
        F.col("_ingestion_ts").asc_nulls_last(), "_source_file", "_record_hash")
    df = df.withColumn("_f_DUP_EXACT", F.row_number().over(first_copy) > 1)
    latest = Window.partitionBy("maintenance_id").orderBy(  # duplicates sort last
        "_f_DUP_EXACT", F.col("record_version").desc_nulls_last(),
        F.col("last_modified_ts").desc_nulls_last(), F.col("_ingestion_ts").desc_nulls_last(),
        "_record_hash")
    return df.withColumn("_f_VERSION_LATEST",
                         ~F.col("_f_DUP_EXACT") & (F.row_number().over(latest) > 1))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Clean (§5.3 – §5.7)

# COMMAND ----------


def resolve_work_order_and_equipment(df: DataFrame, work_orders_df: DataFrame,
                                     equipment_df: DataFrame, cfg: dict = CONFIG) -> DataFrame:
    """Attach the work order as `_wo_*` columns and resolve `equipment_id` (§5.3, §5.4).

    Null work_order_id is valid only on legacy rows. Missing equipment_id → taken from the work
    order; nothing to take → row quarantine. Both set and different → error. An ID not in the
    equipment master → EQ_UNREGISTERED: flagged, value kept (found by lookup, never a literal).
    A recovered ID stays RECOVERED_FROM_WO even if unregistered; `dq_unknown_equipment` still
    shows the miss.
    """
    wo = work_orders_df.select(*[F.col(c).alias(f"_wo_{c}") for c in WORK_ORDER_COLUMNS])
    registered = equipment_df.select(F.col("equipment_id").alias("_w_registered_id")).distinct()
    df = df.join(F.broadcast(wo), F.col("work_order_id") == F.col("_wo_work_order_id"), "left")
    resolved = F.coalesce(F.col("equipment_id"), F.col("_wo_equipment_id"))
    df = df.join(F.broadcast(registered), resolved == F.col("_w_registered_id"), "left")
    has_wo, found = F.col("work_order_id").isNotNull(), F.col("_wo_work_order_id").isNotNull()
    eq, wo_eq = F.col("equipment_id"), F.col("_wo_equipment_id")
    recovered = eq.isNull() & wo_eq.isNotNull()
    unregistered = resolved.isNotNull() & F.col("_w_registered_id").isNull()
    return df.withColumns({  # one projection: every expression sees the original columns
        "_w_wo_found": found,
        "_f_WO_NULL_NON_LEGACY": ~has_wo & ~F.col("source_system").eqNullSafe(
            cfg["legacy_source_system"]),
        "_f_WO_ORPHAN": has_wo & ~found,
        "_f_EQ_MATCHES_WO": _flag(eq != wo_eq),
        "_f_EQ_RECOVERED_FROM_WO": recovered,
        "_f_EQ_MISSING_UNRECOVERABLE": resolved.isNull(),
        "_f_EQ_UNREGISTERED": unregistered,
        "dq_unknown_equipment": unregistered,
        "equipment_id": resolved,
        "equipment_resolution": F.when(recovered, "RECOVERED_FROM_WO")
                                 .when(unregistered, "UNREGISTERED").otherwise("SOURCE"),
    })


def fix_timestamps(df: DataFrame) -> DataFrame:
    """TS_END_BEFORE_START: end < start → end null (field quarantine). The start is the verified
    value: never swap, never estimate the end. The other three checks only warn."""
    start, end, extract = F.col("activity_start_ts"), F.col("activity_end_ts"), F.col("ingestion_ts")
    bad_end = _flag(end < start)
    end = F.when(~bad_end, end)
    return df.withColumns({
        "_f_TS_END_BEFORE_START": bad_end,
        "activity_end_ts": end,
        "_f_TS_AFTER_EXTRACT": _flag((start > extract) | (end > extract)),
        "_f_TS_MODIFIED_BEFORE_END": _flag(F.col("last_modified_ts") < end),
        "_f_NDD_AFTER_END": _flag(F.col("next_due_date") <= F.to_date(F.coalesce(end, start))),
    })


def fix_parts(df: DataFrame, cfg: dict = CONFIG) -> DataFrame:
    """Parts cost (EUR) then quantity: C1 → C2 → C3 → Q1 → Q2 (§5.6). Never abs() a cost.

    C1: no-parts activity with a non-zero cost → 0. C2: negative cost → work order's cost minus
    the other activities' cost, when it is the only bad row on the work order and the result
    is >= 0. C3: otherwise null. Q1: cost 0 → qty 0. Q2: negative qty with a real cost → abs().
    """
    cost, qty = F.col("parts_cost_eur"), F.col("parts_qty")
    c1 = _flag(F.col("activity_type").isin(cfg["no_parts_activity_types"]) & (cost != 0))
    df = df.withColumns({"_f_C1": c1,
                         "_w_cost": F.when(c1, _dec(0, "decimal(12,2)")).otherwise(cost),
                         "_w_cost_bad": _flag(~c1 & (cost < 0))})
    bad = F.col("_w_cost_bad")
    others = _per_work_order(F.sum(F.when(~bad, F.col("_w_cost"))))
    recovered = F.round(F.col("_wo_parts_cost_eur") - F.coalesce(others, _dec(0, "decimal(12,2)")), 2)
    c2 = _flag(bad & (_per_work_order(F.sum(bad.cast("int"))) == 1) & (recovered >= 0))
    df = df.withColumns({
        "_f_C2": c2, "_f_C3": bad & ~c2,
        "parts_cost_eur": F.when(c2, recovered).when(~bad, F.col("_w_cost")).cast("decimal(12,2)"),
    })
    cost = F.col("parts_cost_eur")
    q1, q2 = _flag((cost == 0) & (qty != 0)), _flag((qty < 0) & (cost != 0))
    df = df.withColumns({"_f_Q1": q1, "_f_Q2": q2,
                         "parts_qty": F.when(q1, 0).when(q2, F.abs(qty)).otherwise(qty).cast("int")})
    qty = F.col("parts_qty")
    return df.withColumns({"parts_fix_rule": _applied(PARTS_FIX_LABELS),
                           "_f_PARTS_QTY_COST_CONSISTENT": _flag((qty == 0) != (cost == 0))})


def _valid_factor(hours: Column, duration_h: Column, cfg: dict) -> Column:
    """ratio = |hours| / max(duration, 0.25 h); valid when round(ratio) is an allowed factor
    within the tolerance. False when the duration is unknown."""
    ratio = F.abs(hours) / F.greatest(duration_h, _dec(cfg["min_booking_h"], "decimal(6,2)"))
    factor = F.round(ratio, 0)
    return _flag(duration_h.isNotNull() & factor.isin(cfg["allowed_factors"])
                 & (F.abs(ratio - factor) <= _dec(cfg["factor_tolerance"], "decimal(6,4)")))


def fix_labour(df: DataFrame, cfg: dict = CONFIG) -> DataFrame:
    """Labour hours: L1 → L2 → L3 → L4 (§5.7). Runs after fix_timestamps (a nulled end = no
    duration). Minutes are detected by ratio, never by a "> 60" threshold.

    L1: sentinel (999) → null for L4. L2: ratio/60 is a valid factor → hours/60. L3: negative →
    abs(). L4: work order's hours minus the other activities' hours, when it is the only
    sentinel on the work order and the result is > 0; otherwise null.
    LABOR_UNEXPLAINED_RATIO: after the fixes every row with a duration must have a valid factor.
    """
    hours = F.col("labor_hours")
    seconds = F.unix_timestamp("activity_end_ts") - F.unix_timestamp("activity_start_ts")
    duration_h = seconds.cast("decimal(18,0)") / _dec(3600, "decimal(6,0)")
    minutes = _dec(60, "decimal(4,0)")
    l1 = _flag(hours.isin(cfg["labour_sentinels"]))
    l2 = ~l1 & _valid_factor(hours / minutes, duration_h, cfg)
    fixed = F.when(l2, F.round(hours / minutes, 2)).when(~l1, hours).cast("decimal(8,2)")
    l3 = _flag(fixed < 0)
    df = df.withColumns({"_w_duration_h": duration_h, "_f_L1": l1, "_f_L2": l2, "_f_L3": l3,
                         "_w_hours": F.when(l3, F.abs(fixed)).otherwise(fixed)})

    others = _per_work_order(F.sum(F.when(~F.col("_f_L1"), F.col("_w_hours"))))
    recovered = F.round(F.col("_wo_labor_hours") - F.coalesce(others, _dec(0, "decimal(8,2)")), 2)
    l4 = _flag(F.col("_f_L1") & (_per_work_order(F.sum(F.col("_f_L1").cast("int"))) == 1)
               & (recovered > 0))
    df = df.withColumns({
        "_f_L4_RECOVERED": l4, "_f_L4_UNRECOVERABLE": F.col("_f_L1") & ~l4,
        "labor_hours": F.when(l4, recovered).otherwise(F.col("_w_hours")).cast("decimal(8,2)"),
    })
    hours = F.col("labor_hours")
    return df.withColumns({
        "labor_fix_rule": _applied(LABOR_FIX_LABELS),
        "_f_LABOR_UNEXPLAINED_RATIO": _flag(hours.isNotNull() & F.col("_w_duration_h").isNotNull()
                                            & ~_valid_factor(hours, F.col("_w_duration_h"), cfg)),
    })

# COMMAND ----------
# MAGIC %md
# MAGIC ## Checks (values never change: §5.3, §5.8, §5.9)

# COMMAND ----------


def check_rules(df: DataFrame, cfg: dict = CONFIG) -> DataFrame:
    """Consistency with the work order, expected nulls, code lists and key format.
    Expected nulls (root cause, next due date) are validated, never filled with placeholders."""
    found, planned = F.col("_w_wo_found"), _flag(F.col("failure_mode_code") == "NONE")
    rc_conflict = _flag(found & ~F.col("root_cause_code").eqNullSafe(F.col("_wo_root_cause_code")))
    activities = _per_work_order(F.count(F.lit(1)))
    bad_code = reduce(lambda a, b: a | b, [
        ~_flag(F.col(c).isin(values)) for c, values in cfg["codes"].items()
        if c != "root_cause_code"])
    bad_code = (bad_code | ~_flag(F.col("component_code").isin(list(cfg["component_to_code"].values())))
                | (F.col("root_cause_code").isNotNull()
                   & ~F.col("root_cause_code").isin(cfg["codes"]["root_cause_code"]))
                | ~_flag(F.col("technician_id").rlike(cfg["technician_pattern"])))
    component_map = F.create_map(*[F.lit(x) for kv in cfg["component_to_code"].items() for x in kv])
    expected_code = F.call_function("try_element_at", component_map, F.col("component"))
    return df.withColumns({
        "_f_FM_MATCHES_WO": _flag(
            found & ~F.col("failure_mode_code").eqNullSafe(F.col("_wo_failure_mode_code"))),
        "_f_RC_CONFLICT_WITH_WO": rc_conflict,
        "dq_rc_conflict_with_wo": rc_conflict,  # Gold decides which root cause wins
        "_f_ACTIVITIES_PER_WO": _flag(F.col("work_order_id").isNotNull()
                                      & (activities > cfg["max_activities_per_work_order"])),
        "_f_RC_NULL_IFF_NONE": F.col("root_cause_code").isNull() != planned,
        "_f_NDD_IFF_PLANNED": F.col("next_due_date").isNotNull() != planned,
        "_f_DOMAIN_CODES": _flag(bad_code),
        "_f_COMPONENT_CODE_MAP": ~expected_code.eqNullSafe(F.col("component_code")),
        "_f_SOURCE_KEY_FORMAT": ~F.col("source_system_key").eqNullSafe(
            F.concat_ws("|", "source_system", "maintenance_id")),
    })

# COMMAND ----------
# MAGIC %md
# MAGIC ## Build the outputs (no reads, no writes)

# COMMAND ----------


class SilverBuild(BaseModel):
    """Every output frame of one build. DataFrames are only type-checked (isinstance), not
    parsed; `errors` is validated as (rule, count) pairs."""

    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    silver_df: DataFrame
    quarantine_df: DataFrame
    rule_counts_df: DataFrame
    reconciliation_df: DataFrame
    errors: list[tuple[str, int]]  # error rules above 0, with their counts


def prepare(bronze_df: DataFrame, work_orders_df: DataFrame, equipment_df: DataFrame,
            cfg: dict = CONFIG) -> tuple[DataFrame, DataFrame]:
    """All rules in §4 order. Returns (raw, clean): every Bronze row with the dedup flags, and
    the deduplicated rows with every fix and `_f_<rule>` flag."""
    raw = deduplicate(standardise(bronze_df, cfg))
    clean = raw.filter(~F.col("_f_DUP_EXACT") & ~F.col("_f_VERSION_LATEST"))
    clean = resolve_work_order_and_equipment(clean, work_orders_df, equipment_df, cfg)
    clean = fix_labour(fix_parts(fix_timestamps(clean), cfg), cfg)
    return raw, check_rules(clean, cfg)


def rule_counts(raw: DataFrame, clean: DataFrame) -> DataFrame:
    """rule, severity, batch_id, row_count: per source batch plus batch_id = ALL."""
    def count(df: DataFrame, rules: list[str]) -> DataFrame:
        sums = [F.coalesce(F.sum(F.col(f"_f_{r}").cast("int")), F.lit(0)).cast("long").alias(r)
                for r in rules]
        agg = df.rollup("batch_id").agg(*sums, F.grouping("batch_id").alias("_g"))
        agg = agg.withColumn("batch_id", F.when(F.col("_g") == 1, "ALL")
                             .otherwise(F.col("batch_id"))).drop("_g")
        return agg.unpivot("batch_id", rules, "rule", "row_count")

    severity = F.create_map(*[F.lit(x) for rule, (sev, _, _) in RULES.items() for x in (rule, sev)])
    clean_rules = [r for r in RULES if r not in RAW_RULES]
    return (count(raw, RAW_RULES).unionByName(count(clean, clean_rules))
            .select("rule", severity[F.col("rule")].alias("severity"), "batch_id", "row_count"))


def build_row_flow(raw: DataFrame, silver: DataFrame, counts: DataFrame, run_id: str) -> DataFrame:
    """ROW_FLOW:<batch> (§8.1): Bronze rows = Silver + duplicates + superseded + row quarantine.
    Every Bronze row has exactly one outcome."""
    def per_batch(df: DataFrame, name: str) -> DataFrame:
        agg = df.rollup("batch_id").agg(F.count(F.lit(1)).alias(name),
                                        F.grouping("batch_id").alias("_g"))
        return agg.select(F.when(F.col("_g") == 1, "ALL").otherwise(F.col("batch_id"))
                          .alias("batch_id"), name)

    dropped = (counts.filter(F.col("rule").isin("DUP_EXACT", "VERSION_LATEST",
                                                "EQ_MISSING_UNRECOVERABLE"))
               .groupBy("batch_id").agg(F.sum("row_count").alias("dropped")))
    flow = (per_batch(raw, "bronze").join(per_batch(silver, "silver"), "batch_id", "left")
            .join(dropped, "batch_id", "left").fillna(0, ["silver", "dropped"]))
    left, right, money = F.col("bronze"), F.col("silver") + F.col("dropped"), "decimal(18,2)"
    return flow.select(
        F.lit(run_id).alias("run_id"), F.lit(ENTITY).alias("entity"),
        F.concat(F.lit("ROW_FLOW:"), "batch_id").alias("check_name"),
        F.when(left == right, "MATCH").otherwise("MISMATCH").alias("status"),
        F.col("silver").cast("long").alias("row_count"),
        left.cast(money).alias("left_value"), right.cast(money).alias("right_value"),
        (left - right).cast(money).alias("diff"), F.current_timestamp().alias("run_ts"))


def build_quarantine(clean: DataFrame, run_id: str) -> DataFrame:
    """Quarantine records (§6.3), status OPEN. ROW: the whole delivered row. FIELD: the
    delivered value of the nulled field, only for rows that stay in Silver."""
    frames = []
    for rule, field in QUARANTINE_FIELDS.items():
        rows = clean.filter(F.col(f"_f_{rule}"))
        if field is None:
            original = F.col("_w_source_json")
        else:
            rows = rows.filter(~F.col("_f_EQ_MISSING_UNRECOVERABLE"))
            original = F.to_json(F.struct(
                F.get_json_object("_w_source_json", f"$.{field}").alias(field)))
        frames.append(rows.select(
            F.lit(ENTITY).alias("entity"), F.col("maintenance_id").alias("record_id"),
            F.lit(rule).alias("rule_id"), F.lit("ROW" if field is None else "FIELD")
            .alias("quarantine_type"), F.lit(field).cast("string").alias("field_name"),
            F.lit(RULES[rule][2]).alias("reason"), original.alias("original_values"),
            F.col("record_version").cast("int"), "_batch_id", "_source_file"))
    now = F.current_timestamp()
    return reduce(DataFrame.unionByName, frames).select(
        "*", now.alias("quarantine_ts"), now.alias("last_seen_ts"),
        F.lit(run_id).alias("last_run_id"), F.lit("OPEN").alias("resolution_status"),
        F.lit(None).cast("timestamp").alias("resolved_ts"))


def build_silver(bronze_df: DataFrame, work_orders_df: DataFrame, equipment_df: DataFrame,
                 cfg: dict = CONFIG, run_id: str = "local") -> SilverBuild:
    """Every output frame. `bronze_df` must be filtered on the Bronze success status; the
    Spark session time zone must be UTC. Collects only the error counts."""
    raw, clean = prepare(bronze_df, work_orders_df, equipment_df, cfg)
    silver = (clean.filter(~F.col("_f_EQ_MISSING_UNRECOVERABLE"))
              .select(*[F.col(n).cast(t).alias(n) for n, t, _ in SILVER_SCHEMA
                        if not n.startswith("_silver_")])
              .withColumns({"_silver_run_id": F.lit(run_id),
                            "_silver_processed_ts": F.current_timestamp(),
                            "_silver_row_hash": _sha256(ROW_HASH_COLUMNS)})
              .select(*SILVER_COLUMNS))
    counts = rule_counts(raw, clean)
    errors = [(r["rule"], r["row_count"]) for r in counts.filter(
        (F.col("severity") == ERROR) & (F.col("batch_id") == "ALL") & (F.col("row_count") > 0)
    ).collect()]
    return SilverBuild(silver_df=silver, quarantine_df=build_quarantine(clean, run_id),
                       rule_counts_df=counts,
                       reconciliation_df=build_row_flow(raw, silver, counts, run_id),
                       errors=errors)

# COMMAND ----------
# MAGIC %md
# MAGIC ## Writes (Databricks only)

# COMMAND ----------


def write_silver(spark: SparkSession, build: SilverBuild, table: str) -> dict[str, int]:
    """MERGE on maintenance_id (§6.1). Update on a newer version or a changed row hash, insert
    new rows, delete rows no longer produced. Unchanged data → 0 / 0 / 0 (idempotency)."""
    from delta.tables import DeltaTable

    def sql_text(text: str) -> str:
        return text.replace("\\", "\\\\").replace("'", "\\'")

    columns = ",\n  ".join(f"`{n}` {t} COMMENT '{sql_text(c)}'" for n, t, c in SILVER_SCHEMA)
    spark.sql(f"CREATE TABLE IF NOT EXISTS {table} (\n  {columns}\n) USING DELTA "
              "COMMENT 'Silver maintenance activities: one row per activity, latest version' "
              "TBLPROPERTIES ('delta.enableChangeDataFeed' = 'true')")
    if set(spark.table(table).columns) != set(SILVER_COLUMNS):  # e.g. an older schema
        raise RuntimeError(f"{table} columns differ from SILVER_SCHEMA: DROP TABLE {table} "
                           "and rerun to recreate it")
    target = DeltaTable.forName(spark, table)
    (target.alias("t").merge(build.silver_df.alias("s"), "t.maintenance_id = s.maintenance_id")
     .whenMatchedUpdateAll(condition="s.record_version > t.record_version OR "
                                     "(s.record_version = t.record_version AND "
                                     "s._silver_row_hash <> t._silver_row_hash)")
     .whenNotMatchedInsertAll()
     .whenNotMatchedBySourceDelete()
     .execute())
    metrics = target.history(1).select("operationMetrics").first()["operationMetrics"]
    return {k: int(metrics.get(f"numTargetRows{k.capitalize()}", 0))
            for k in ["inserted", "updated", "deleted"]}


def write_quarantine(spark: SparkSession, build: SilverBuild, table: str, run_id: str) -> None:
    """MERGE on (entity, record_id, rule_id), never append (§6.3). Still firing → refresh and
    reopen; new → insert; this entity's OPEN records that stopped firing → RESOLVED."""
    from delta.tables import DeltaTable

    if not spark.catalog.tableExists(table):
        build.quarantine_df.limit(0).write.format("delta").saveAsTable(table)
    keep_first_seen = {c: f"s.{c}" for c in build.quarantine_df.columns if c != "quarantine_ts"}
    (DeltaTable.forName(spark, table).alias("t")
     .merge(build.quarantine_df.alias("s"),
            "t.entity = s.entity AND t.record_id = s.record_id AND t.rule_id = s.rule_id")
     .whenMatchedUpdate(set=keep_first_seen)
     .whenNotMatchedInsertAll()
     .whenNotMatchedBySourceUpdate(
         condition=f"t.entity = '{ENTITY}' AND t.resolution_status = 'OPEN'",
         set={"resolution_status": "'RESOLVED'", "resolved_ts": "current_timestamp()",
              "last_run_id": f"'{run_id}'"})
     .execute())


def append_dq_results(spark: SparkSession, build: SilverBuild, table: str, run_id: str,
                      rows_read: int, rows_after_dedup: int) -> None:
    """Append this run's totals to the team's dq_results table."""
    info = spark.createDataFrame([(r, dim, desc) for r, (_, dim, desc) in RULES.items()],
                                 "rule string, dimension string, description string")
    evaluated = F.when(F.col("rule").isin(RAW_RULES), rows_read).otherwise(rows_after_dedup)
    failed = F.col("row_count").cast("long")
    (build.rule_counts_df.filter(F.col("batch_id") == "ALL").join(info, "rule")
     .select(F.lit(f"silver_{ENTITY}").alias("table_name"), F.col("rule").alias("rule_name"),
             "dimension", "severity", "description",
             evaluated.cast("long").alias("records_evaluated"), failed.alias("records_failed"),
             F.round(failed / evaluated, 4).alias("failure_rate"),
             F.current_timestamp().alias("run_ts"), F.lit(run_id).alias("run_id"))
     .write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(table))

# COMMAND ----------
# MAGIC %md
# MAGIC ## Run

# COMMAND ----------


def main(spark: SparkSession, dbutils, display) -> None:
    spark.conf.set("spark.sql.session.timeZone", "UTC")
    w = dbutils.widgets
    w.text("catalog", "data2026acc123456", "Catalog")
    w.text("schema", "team3", "Schema")
    w.text("bronze_table", "bronze_maintenance_history", "Bronze table")
    w.text("work_orders_table", CONFIG["tables"]["work_orders"], "Work orders silver table")
    w.text("equipment_table", CONFIG["tables"]["equipment"], "Equipment silver table")

    def qualify(name: str) -> str:
        return name if "." in name else f"{w.get('catalog')}.{w.get('schema')}.{name}"

    tables = {k: qualify(v) for k, v in {**CONFIG["tables"], "bronze": w.get("bronze_table"),
                                          "work_orders": w.get("work_orders_table"),
                                          "equipment": w.get("equipment_table")}.items()}
    run_id = f"{ENTITY}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    print(f"run_id={run_id} tables={json.dumps(tables)}")

    bronze_all = adapt_bronze_columns(spark.table(tables["bronze"]))
    work_orders = spark.table(tables["work_orders"])  # Silver only (§3.1)
    equipment = spark.table(tables["equipment"]).select("equipment_id")  # only the IDs
    check_contracts(bronze_all, work_orders, equipment)
    bronze = bronze_all.filter(F.col("_load_status") == CONFIG["bronze_loaded_status"])
    rows_in_bronze, rows_read = bronze_all.count(), bronze.count()
    if rows_read == 0:  # a wrong status filter would otherwise MERGE-delete all of Silver
        raise RuntimeError(f"No Bronze rows with _load_status = "
                           f"'{CONFIG['bronze_loaded_status']}' in {tables['bronze']}")
    if rows_read < rows_in_bronze:
        print(f"WARNING: {rows_in_bronze - rows_read} Bronze rows not loaded; they stay in Bronze")

    build = build_silver(bronze, work_orders, equipment, CONFIG, run_id)
    display(build.rule_counts_df)
    display(build.reconciliation_df)
    totals = {r["rule"]: r["row_count"]
              for r in build.rule_counts_df.filter(F.col("batch_id") == "ALL").collect()}
    after_dedup = rows_read - totals["DUP_EXACT"] - totals["VERSION_LATEST"]

    if build.errors:  # error gate (§6.2): DQ results only, Silver untouched
        append_dq_results(spark, build, tables["audit"], run_id, rows_read, after_dedup)
        failed = ", ".join(f"{rule}={n}" for rule, n in build.errors)
        raise RuntimeError(f"Error rules fired, Silver not written: {failed} (run {run_id})")

    merge = write_silver(spark, build, tables["silver"])
    write_quarantine(spark, build, tables["quarantine"], run_id)
    append_dq_results(spark, build, tables["audit"], run_id, rows_read, after_dedup)
    build.reconciliation_df.write.format("delta").mode("append").saveAsTable(
        tables["reconciliation"])
    print(json.dumps({"run_id": run_id, "rows_read": rows_read, "after_dedup": after_dedup,
                      **{f"silver_{k}": v for k, v in merge.items()}}))

    # Before/after for every fixed row (§6.5): Silver keeps no raw copies, Bronze does.
    display(spark.sql(f"""
        SELECT s.maintenance_id, s.parts_fix_rule, b.parts_cost_eur AS parts_cost_before,
               s.parts_cost_eur AS parts_cost_after, s.labor_fix_rule,
               b.labor_hours AS labor_hours_before, s.labor_hours AS labor_hours_after
        FROM {tables['silver']} s
        JOIN (SELECT DISTINCT record_hash AS _record_hash, parts_cost_eur, labor_hours
              FROM {tables['bronze']}) b ON b._record_hash = s._record_hash
        WHERE s.parts_fix_rule <> 'UNCHANGED' OR s.labor_fix_rule <> 'UNCHANGED'"""))

# COMMAND ----------

if "dbutils" in globals():  # true in a Databricks notebook, false when pytest imports the file
    main(spark, dbutils, display)  # noqa: F821
