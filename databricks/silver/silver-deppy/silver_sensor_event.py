"""

Run over stable bronze_sensor_events input ingested from JSON. Full refresh outputs.
Shared conventions: Config, SOURCE_COLS/LINEAGE/CASTS, _f_* flags,
clean_bronze_table(), convert_to_silver_table(), main(), UTC, uppercase equipment IDs.
Sensor business key: (source_system, event_id), preserving event/source ID case.
Exact duplicates are compared within a source, excluding ingestion lineage.
All Bronze rows are counted; rescued/non-SUCCESS rows are filtered from Silver.
Missing equipment stays NULL / MISSING in retained rows. Rejected rows get one ROW record
per failed rule in the shared silver_dq_quarantine (same schema as silver_maintenance.py).
Equipment registration is not checked; equipment_id comes only from Bronze.
TYPE_CAST flags identify nonempty values that failed conversion; required sensor
fields are rejected by their DQ rules, optional fields remain NULL with evidence.
No timestamp repair is invented: a sensor event has no maintenance duration.
No fallback to an older valid event when the latest version is invalid.

The supplied silver_maintenance.py is a Work Orders implementation (including its
input/output tables), not a Maintenance History implementation. This script does
not certify or correct that file. It does not need Work Orders or Maintenance.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from pyspark.sql import DataFrame, Window, functions as F
from pyspark.sql.types import StructType


# ---------------------------------------------------------------- 1. Config & Setup
class Config:
    base = "data2026acc123456.team3"
    bronze_loaded_status = "SUCCESS"
    bronze_column_map = {
        "ingestion_timestamp": "_ingestion_ts",
        "batch_id": "_batch_id",
        "source_filename": "_source_file",
        "record_hash": "_record_hash",
        "load_status": "_load_status",
    }


CFG = Config()


# ---------------------------------------------------------------- 2. Columns & Expressions
REQUIRED_PATHS = ['event_id',
 'record_version',
 'event_ts',
 'equipment_id',
 'operating_state',
 'sensor.sensor_id',
 'sensor.sensor_type',
 'sensor.measure',
 'sensor.canonical_unit',
 'sensor.range_min',
 'sensor.range_max',
 'sensor.measurement_point',
 'reading.value',
 'reading.unit',
 'quality_flag',
 'alarm.state',
 'alarm.code',
 'alarm.limit_value',
 'gateway.gateway_id',
 'gateway.firmware_version',
 'gateway.received_ts',
 'location.site_code',
 'location.production_line',
 'tags',
 '_metadata.source_system',
 '_metadata.source_system_key',
 '_metadata.source_file',
 '_metadata.batch_id',
 '_metadata.ingestion_ts',
 'record_hash',
 'ingestion_timestamp',
 'source_filename',
 'batch_id',
 'load_status']
SOURCE_COLS = ['event_id',
 'record_version',
 'event_timestamp',
 'equipment_id',
 'operating_state',
 'sensor_id',
 'sensor_type',
 'measure',
 'canonical_unit',
 'range_min',
 'range_max',
 'measurement_point',
 'reading_value_raw',
 'reading_value',
 'reading_unit',
 'quality_flag',
 'alarm_state',
 'alarm_code',
 'alarm_limit_value',
 'gateway_id',
 'firmware_version',
 'received_timestamp',
 'site_code',
 'production_line',
 'tags',
 'source_system',
 'source_system_key',
 'source_file',
 'source_batch_id',
 'source_ingestion_timestamp']
CASTS = {'record_version': ('record_version', 'INT'),
 'event_timestamp': ('event_ts', 'TIMESTAMP'),
 'range_min': ('sensor.range_min', 'DOUBLE'),
 'range_max': ('sensor.range_max', 'DOUBLE'),
 'reading_value': ('reading.value', 'DOUBLE'),
 'alarm_limit_value': ('alarm.limit_value', 'DOUBLE'),
 'received_timestamp': ('gateway.received_ts', 'TIMESTAMP'),
 'source_ingestion_timestamp': ('_metadata.ingestion_ts', 'TIMESTAMP'),
 'bronze_ingestion_timestamp': ('ingestion_timestamp', 'TIMESTAMP')}
LINEAGE = ["source_system", "source_system_key", "source_file",
           "source_batch_id", "source_ingestion_timestamp"]
BUSINESS = [c for c in SOURCE_COLS if c not in LINEAGE]


def B(expression):
    return F.coalesce(expression, F.lit(False))


def FLAG(name):
    return f"_f_{name}"


def fired(pairs):
    return F.filter(F.array(*[F.when(B(condition), F.lit(name))
                             for name, condition in pairs]),
                    lambda value: value.isNotNull())


# Shared silver_dq_quarantine schema: identical in silver_maintenance.py and silver-stau.py
# (spec: context/silver_max/silver_maintenance_rules.md 6.3). Change all three together.
QUARANTINE_DDL = """CREATE TABLE IF NOT EXISTS {table} (
    entity STRING, record_id STRING, rule_id STRING, quarantine_type STRING, field_name STRING,
    reason STRING, original_values STRING, record_version INT, _batch_id STRING,
    _source_file STRING, quarantine_ts TIMESTAMP, last_seen_ts TIMESTAMP, last_run_id STRING,
    resolution_status STRING, resolved_ts TIMESTAMP
) USING DELTA"""
QUARANTINE_ENTITY = "sensor_events"


@dataclass
class CleaningResult:
    silver: DataFrame
    superseded: DataFrame
    quarantine: DataFrame
    audit: DataFrame
    counts: dict
    run_id: str


# ---------------------------------------------------------------- 3. Core Functions

def _check_schema(df: DataFrame, paths, label: str) -> None:
    missing = []
    for path in paths:
        current = df.schema
        for part in path.split("."):
            if not isinstance(current, StructType) or part not in current.fieldNames():
                missing.append(path)
                break
            current = current[part].dataType
    if missing:
        raise ValueError(f"SCHEMA_CONTRACT: {label} missing/invalid paths: {missing}")


def _read_and_standardise(bronze: DataFrame) -> DataFrame:
    _check_schema(bronze, REQUIRED_PATHS, "bronze_sensor_events")
    if "_rescued_data" not in bronze.columns:
        bronze = bronze.withColumn("_rescued_data", F.lit(None).cast("string"))
    # Snapshot cast failures before standardization loses the original values.
    cast_errors = []
    for name, (path, dtype) in CASTS.items():
        quoted = ".".join(f"`{p}`" for p in path.split("."))
        raw = F.trim(F.col(quoted).cast("string"))
        cast_errors.append((name, raw.isNotNull() & (raw != "") &
                            F.expr(f"try_cast({quoted} AS {dtype})").isNull()))
    bronze = bronze.withColumn("_type_cast_fields", fired(cast_errors))
    df = bronze.selectExpr(
        # Event identifiers and timestamps
        "trim(event_id) AS event_id",
        "try_cast(record_version AS INT) AS record_version",
        "try_cast(event_ts AS TIMESTAMP) AS event_timestamp",
        "nullif(trim(equipment_id), '') AS equipment_id",
        "lower(trim(operating_state)) AS operating_state",

        # Sensor details
        "trim(sensor.sensor_id) AS sensor_id",
        "lower(trim(sensor.sensor_type)) AS sensor_type",
        "trim(sensor.measure) AS measure",
        "trim(sensor.canonical_unit) AS canonical_unit",
        "try_cast(sensor.range_min AS DOUBLE) AS range_min",
        "try_cast(sensor.range_max AS DOUBLE) AS range_max",
        "trim(sensor.measurement_point) AS measurement_point",

        # Reading: preserve original value and unit
        "reading.value AS reading_value_raw",
        "try_cast(reading.value AS DOUBLE) AS reading_value",
        "trim(reading.unit) AS reading_unit",

        # Quality and alarm details
        "upper(trim(quality_flag)) AS quality_flag",
        "lower(trim(alarm.state)) AS alarm_state",
        "trim(alarm.code) AS alarm_code",
        "try_cast(alarm.limit_value AS DOUBLE) AS alarm_limit_value",

        # Gateway and location
        "trim(gateway.gateway_id) AS gateway_id",
        "gateway.firmware_version AS firmware_version",
        "try_cast(gateway.received_ts AS TIMESTAMP) AS received_timestamp",
        "upper(trim(location.site_code)) AS site_code",
        "trim(location.production_line) AS production_line",
        "tags",

        # Source metadata
        "nullif(trim(`_metadata`.source_system), '') AS source_system",
        "`_metadata`.source_system_key AS source_system_key",
        "`_metadata`.source_file AS source_file",
        "`_metadata`.batch_id AS source_batch_id",
        "try_cast(`_metadata`.ingestion_ts AS TIMESTAMP) AS source_ingestion_timestamp",

        # Bronze metadata for traceability
        "record_hash",
        "try_cast(ingestion_timestamp AS TIMESTAMP) AS bronze_ingestion_timestamp",
        "source_filename",
        "batch_id AS bronze_batch_id",
        "load_status",
        "_rescued_data",

        # Original record for investigation and quarantine
        "to_json(struct(*)) AS raw_record_json",
        "_type_cast_fields"
    )

    # All projected string fields: trim and empty -> NULL. Keep raw evidence intact.
    df = df.select(*[
        F.when(F.trim(F.col(name)) == "", None).otherwise(F.trim(F.col(name))).alias(name)
        if dtype == "string" and name not in ["raw_record_json", "_rescued_data"]
        else F.col(name)
        for name, dtype in df.dtypes
    ])
    return (df.withColumn("equipment_id", F.upper("equipment_id"))
            .withColumn(FLAG("TYPE_CAST"), F.size("_type_cast_fields") > 0))


def _deduplicate(df: DataFrame) -> DataFrame:
    # Include invalid cast originals so different malformed inputs do not collapse
    # into an identical NULL business payload after casting.
    invalid_originals = F.struct(*[
        F.when(F.array_contains("_type_cast_fields", name),
               F.get_json_object("raw_record_json", "$." + path)).alias(name)
        for name, (path, _) in CASTS.items()
        if name in BUSINESS
    ])
    business_json = F.to_json(F.struct(
        *[F.col(name) for name in BUSINESS], invalid_originals.alias("cast_originals")),
        options={"ignoreNullFields": "false"})
    df = df.withColumn("_business_json", business_json)
    eligible = (F.col("event_id").isNotNull() & F.col("source_system").isNotNull()
                & B(F.col("load_status") == CFG.bronze_loaded_status)
                & F.col("_rescued_data").isNull())
    # Do not let malformed/rescued rows displace a successfully ingested event.
    df = df.withColumn("_dedup_eligible", eligible)
    w1 = Window.partitionBy("source_system", "_business_json", "_dedup_eligible").orderBy(
        F.col("bronze_ingestion_timestamp").asc_nulls_last(),
        F.col("source_filename").asc_nulls_last(),
        F.col("record_hash").asc_nulls_last(), "raw_record_json")
    df = df.withColumn(FLAG("DUP_EXACT"), eligible & (F.row_number().over(w1) > 1))
    w2 = Window.partitionBy("source_system", "event_id", "_dedup_eligible",
                            FLAG("DUP_EXACT")).orderBy(
        F.col("record_version").desc_nulls_last(),
        F.col("source_ingestion_timestamp").desc_nulls_last(),
        F.col("bronze_ingestion_timestamp").desc_nulls_last(),
        F.col("record_hash").asc_nulls_last(), "raw_record_json")
    return (df.withColumn(FLAG("VERSION_LATEST"), eligible &
                         ~F.col(FLAG("DUP_EXACT")) & (F.row_number().over(w2) > 1))
            .drop("_business_json", "_dedup_eligible"))


def clean_bronze_table(bronze: DataFrame) -> CleaningResult:
    """Standardize, deduplicate, validate and route; do not write tables."""
    bronze.sparkSession.conf.set("spark.sql.session.timeZone", "UTC")
    RUN_ID = f"se-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"
    if bronze.limit(1).count() == 0:
        raise ValueError("Empty Bronze: stopped before replacing Silver outputs")
    raw = _deduplicate(_read_and_standardise(bronze))
    excluded = F.col(FLAG("DUP_EXACT")) | F.col(FLAG("VERSION_LATEST"))
    df_superseded = (raw.filter(excluded).withColumn("superseded_reason",
        F.when(F.col(FLAG("DUP_EXACT")), "DUP_EXACT").otherwise("VERSION_LATEST")))
    df_cleaned = raw.filter(~excluded)
    input_count = raw.count()
    superseded_count = df_superseded.count()
    deduped_count = df_cleaned.count()
    assert input_count == superseded_count + deduped_count
    df_normalized = df_cleaned.withColumn(
        "reading_value_canonical",

        # Already in the correct unit
        F.when(
            F.col("reading_unit") == F.col("canonical_unit"),
            F.col("reading_value")
        )

        # Fahrenheit -> Celsius
        .when(
            (F.col("reading_unit") == "degF") &
            (F.col("canonical_unit") == "degC"),
            (F.col("reading_value") - 32.0) * 5.0 / 9.0
        )

        # Inches/second -> Millimeters/second
        .when(
            (F.col("reading_unit") == "in/s") &
            (F.col("canonical_unit") == "mm/s"),
            F.col("reading_value") * 25.4
        )

        # PSI -> Bar
        .when(
            (F.col("reading_unit") == "psi") &
            (F.col("canonical_unit") == "bar"),
            F.col("reading_value") * 0.0689475729
        )

        # Unsupported or missing unit: flag later in quality checks
        .otherwise(F.lit(None).cast("double"))
    )

    # Use only equipment_id from the JSON already ingested into Bronze.
    # SOURCE means provided by the source, not verified against a master table.
    df_deduped = (
        df_normalized
        .withColumn(
            "equipment_resolution",
            F.when(F.col("equipment_id").isNull(), F.lit("MISSING"))
             .otherwise(F.lit("SOURCE"))
        )
        .withColumn(
            FLAG("EQ_MISSING"),
            F.col("equipment_id").isNull()
        )
    )
    def missing_text(column_name):
        return (
            F.col(column_name).isNull() |
            (F.trim(F.col(column_name)) == "")
        )


    def invalid_number(column_name):
        return (
            F.col(column_name).isNull() |
            F.isnan(F.col(column_name)) |
            (F.abs(F.col(column_name)) == F.lit(float("inf")))
        )


    # Each condition describes a quality failure.
    rules = [
        (
            "DQ01: Missing event_id",
            missing_text("event_id")
        ),
        (
            "DQ02: Missing source_system",
            missing_text("source_system")
        ),
        # DQ03 retired: missing equipment is EQ_MISSING (FLAG only).
        # DQ04 not evaluated: no equipment reference lookup.
        (
            "DQ05: Missing sensor_id",
            missing_text("sensor_id")
        ),
        (
            "DQ06: Missing or invalid event timestamp",
            F.col("event_timestamp").isNull()
        ),
        (
            "DQ07: Missing or invalid record_version",
            F.col("record_version").isNull() |
            (F.col("record_version") < 1)
        ),
        (
            "DQ08: Missing, invalid reading or unsupported unit conversion",
            invalid_number("reading_value_canonical")
        ),
        (
            "DQ09: Missing or invalid sensor range",
            invalid_number("range_min") |
            invalid_number("range_max") |
            (F.col("range_min") > F.col("range_max"))
        ),
        (
            "DQ10: Reading outside sensor range",
            (F.col("reading_value_canonical") < F.col("range_min")) |
            (F.col("reading_value_canonical") > F.col("range_max"))
        ),
        (
            "DQ11: Missing or invalid operating_state",
            F.col("operating_state").isNull() |
            (~F.col("operating_state").isin(
                "running", "stopped", "maintenance"
            ))
        ),
        (
            "DQ12: Missing or invalid alarm_state",
            F.col("alarm_state").isNull() |
            (~F.col("alarm_state").isin(
                "normal", "warning", "alarm"
            ))
        ),
        (
            "DQ13: Rescued or non-SUCCESS source data requires review",
            F.col("_rescued_data").isNotNull() |
            ~B(F.col("load_status") == CFG.bronze_loaded_status)
        )
    ]

    # Build an array containing every failed rule.
    df_evaluated = (
        df_deduped
        .withColumn(
            "dq_errors",
            F.filter(
                F.array(*[
                    F.when(
                        F.coalesce(condition, F.lit(False)),
                        F.lit(reason)
                    )
                    for reason, condition in rules
                ]),
                lambda reason: reason.isNotNull()
            )
        )
        .withColumn(
            "is_valid",
            F.size(F.col("dq_errors")) == 0
        )
    )

    # Valid sensor events.
    # Retain quality_flag and alarm_state for downstream analysis.
    df_valid = (
        df_evaluated
        .filter(F.col("is_valid"))
        .drop("is_valid", "dq_errors")
        .withColumn("silver_processed_ts", F.current_timestamp())
        .withColumn("silver_run_id", F.lit(RUN_ID))
    )

    # One ROW record per rejected row and failed rule: "DQ10: Reading outside..." -> rule + reason.
    failure = F.split(F.col("failure"), ": ", 2)
    now = F.current_timestamp()
    df_quarantine = (df_evaluated.filter(~F.col("is_valid"))
        .select(F.explode("dq_errors").alias("failure"), "event_id", "record_hash",
                "record_version", "bronze_batch_id", "source_filename", "raw_record_json")
        .select(
            F.lit(QUARANTINE_ENTITY).alias("entity"),
            F.coalesce("event_id", "record_hash").alias("record_id"),
            failure[0].alias("rule_id"), F.lit("ROW").alias("quarantine_type"),
            F.lit(None).cast("string").alias("field_name"), failure[1].alias("reason"),
            F.col("raw_record_json").alias("original_values"), "record_version",
            F.col("bronze_batch_id").alias("_batch_id"),
            F.col("source_filename").alias("_source_file"),
            now.alias("quarantine_ts"), now.alias("last_seen_ts"),
            F.lit(RUN_ID).alias("last_run_id"), F.lit("OPEN").alias("resolution_status"),
            F.lit(None).cast("timestamp").alias("resolved_ts")))

    valid_count = df_valid.count()
    rejected_count = df_evaluated.filter(~F.col("is_valid")).count()
    missing_equipment_count = df_valid.filter(F.col("equipment_id").isNull()).count()
    assert deduped_count == valid_count + rejected_count, "DQ row reconciliation failed"
    print(f"Silver: {valid_count}; filtered invalid: {rejected_count}; "
          f"retained missing equipment: {missing_equipment_count}")
    dimensions = {
        "DQ01": "completeness", "DQ02": "completeness",
        "DQ05": "completeness", "DQ06": "validity", "DQ07": "validity",
        "DQ08": "validity", "DQ09": "validity", "DQ10": "validity",
        "DQ11": "validity", "DQ12": "validity", "DQ13": "validity",
    }
    failed = {r["rule"]: r["count"] for r in df_evaluated.select(
        F.explode("dq_errors").alias("rule")).groupBy("rule").count().collect()}
    audit_rows = [("silver_sensor_events", reason.split(":", 1)[0],
                   dimensions[reason.split(":", 1)[0]], "ERROR", reason,
                   deduped_count, failed.get(reason, 0)) for reason, _ in rules]
    audit_rows += [
        ("silver_sensor_events", "EQ_MISSING", "completeness", "FLAG",
         "Missing equipment_id; retain with MISSING unless another rule rejects the event",
         deduped_count, df_evaluated.filter(F.col("equipment_id").isNull()).count()),
        ("silver_sensor_events", "DUP_EXACT", "uniqueness", "INFO",
         "Exact business duplicates set aside", input_count, raw.filter(F.col(FLAG("DUP_EXACT"))).count()),
    ]
    audit_rows += [
        ("silver_sensor_events", "VERSION_LATEST", "uniqueness", "INFO",
         "Older event versions set aside", input_count,
         raw.filter(F.col(FLAG("VERSION_LATEST"))).count()),
        ("silver_sensor_events", "TYPE_CAST", "validity", "FLAG",
         "Nonempty source values failed casting; details in _type_cast_fields",
         input_count, raw.filter(F.col(FLAG("TYPE_CAST"))).count()),
        ("silver_sensor_events", "SCHEMA_CONTRACT", "validity", "ERROR",
         "Required input paths checked before processing", input_count, 0),
    ]
    dq_df = (bronze.sparkSession.createDataFrame(audit_rows,
        "table_name string, rule_name string, dimension string, severity string, description string, "
        "records_evaluated long, records_failed long")
        .withColumn("failure_rate", F.when(F.col("records_evaluated") > 0,
            F.round(F.col("records_failed") / F.col("records_evaluated"), 4)))
        .withColumn("run_ts", F.current_timestamp()).withColumn("run_id", F.lit(RUN_ID)))
    return CleaningResult(
        silver=df_valid, superseded=df_superseded, quarantine=df_quarantine,
        audit=dq_df, run_id=RUN_ID,
        counts={"input": input_count, "silver": valid_count,
                "rejected": rejected_count,
                "missing_equipment_retained": missing_equipment_count,
                "superseded": superseded_count})


def _with_silver_metadata(df: DataFrame, run_id: str) -> DataFrame:
    # Keep sensor column names; add the common lineage names used by Work Orders.
    aliases = {
        "source_ingestion_timestamp": "ingestion_ts", "source_batch_id": "batch_id",
        "bronze_ingestion_timestamp": "_ingestion_ts", "bronze_batch_id": "_batch_id",
        "source_filename": "_source_file", "record_hash": "_record_hash",
        "source_system": "_source_system", "load_status": "_load_status",
    }
    for source, target in aliases.items():
        df = df.withColumn(target, F.col(source))
    hash_cols = BUSINESS + ["source_system", "reading_value_canonical",
                            "equipment_resolution"]
    # JSON avoids separator/null-token collisions in concat-based row hashes.
    return (df.withColumn("_silver_row_hash", F.sha2(F.to_json(
                F.struct(*[F.col(c) for c in hash_cols]),
                options={"ignoreNullFields": "false"}), 256))
            .withColumn("_silver_run_id", F.lit(run_id))
            .withColumn("_silver_processed_ts", F.current_timestamp())
            .drop("silver_run_id", "silver_processed_ts"))


def merge_quarantine(spark, quarantine: DataFrame, target: str) -> None:
    """MERGE, never append: reruns refresh last_seen_ts, records that stop firing turn RESOLVED."""
    spark.sql(QUARANTINE_DDL.format(table=target))
    # Rows without event_id share record_hash when Bronze holds identical copies.
    quarantine.dropDuplicates(["record_id", "rule_id"]).createOrReplaceTempView(
        "_quarantine_updates")
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


def convert_to_silver_table(cleaned: CleaningResult, base: str = CFG.base) -> None:
    """Validate row reconciliation, write entity outputs, append shared audit."""
    spark = cleaned.silver.sparkSession
    counts = cleaned.counts
    assert counts["input"] == (counts["silver"] + counts["rejected"]
                                + counts["superseded"]), "Row reconciliation failed"
    assert cleaned.silver.groupBy("source_system", "event_id").count().filter(
        F.col("count") > 1).limit(1).count() == 0, "Duplicate Silver event keys"
    assert cleaned.silver.filter(
        F.col("equipment_id").isNull()
        & ~F.col("equipment_resolution").eqNullSafe("MISSING")
    ).limit(1).count() == 0, "Inconsistent missing-equipment flags"
    assert cleaned.silver.filter(F.col("equipment_id").isNull()).count() == counts["missing_equipment_retained"]

    audit_table = f"{base}.dq_results"
    if spark.catalog.tableExists(audit_table):
        existing = {f.name: f.dataType for f in spark.table(audit_table).schema}
        incompatible = [f.name for f in cleaned.audit.schema
                        if f.name in existing and existing[f.name] != f.dataType]
        if incompatible:
            raise ValueError(f"Incompatible dq_results column types: {incompatible}")

    # Full refresh; each Delta write is atomic, not the group of writes.
    for name, frame, expected in [
        ("silver_sensor_events", cleaned.silver, counts["silver"]),
        ("superseded_sensor_events", cleaned.superseded, counts["superseded"]),
    ]:
        final = _with_silver_metadata(frame, cleaned.run_id) if name == "silver_sensor_events" else (
            frame.withColumn("_silver_run_id", F.lit(cleaned.run_id))
                 .withColumn("_silver_processed_ts", F.current_timestamp())
                 .drop("silver_run_id"))
        table = f"{base}.{name}"
        final.write.format("delta").mode("overwrite").option(
            "overwriteSchema", "true").saveAsTable(table)
        assert spark.table(table).count() == expected, f"Saved row count mismatch: {table}"
        print(f"{table}: {expected} records saved")
    merge_quarantine(spark, cleaned.quarantine, f"{base}.silver_dq_quarantine")
    cleaned.audit.write.format("delta").mode("append").option(
        "mergeSchema", "true").saveAsTable(audit_table)
    print(f"Run {cleaned.run_id}: {counts}")
    print("Missing-equipment rows are included in Silver; invalid rows are in silver_dq_quarantine.")


# ---------------------------------------------------------------- 4. Main Execution

def main():
    """Run in Databricks with an active Spark session."""
    from pyspark.sql import SparkSession
    spark = SparkSession.getActiveSession()
    if spark is None:
        raise RuntimeError("Run this script in Databricks with an active Spark session")
    cleaned = clean_bronze_table(
        bronze=spark.table(f"{CFG.base}.bronze_sensor_events")
    )
    convert_to_silver_table(cleaned)


if __name__ == "__main__":
    main()