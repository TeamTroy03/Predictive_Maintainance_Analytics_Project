import uuid
from datetime import UTC, datetime

from delta.tables import DeltaTable
from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F
from pyspark.sql.types import DecimalType, IntegerType, StringType

# ---------------------------------------------------------------- 1. Config & Setup
CONFIG = {
    "bronze_loaded_status": "SUCCESS",
    "bronze_column_map": {
        "batch_id": "_batch_id",
        "source_batch_id": "batch_id",
        "ingestion_timestamp": "_ingestion_ts",
        "source_filename": "_source_file",
        "record_hash": "_record_hash",
        "load_status": "_load_status",
    },
    "timestamp_pattern": "yyyy-MM-dd'T'HH:mm:ss'Z'",
    "date_pattern": "yyyy-MM-dd",
    "legacy_source_system": "LEGACY_CMMS",
    "no_parts_activity_types": ["Inspect", "Align", "Adjust", "Calibrate"],
    "labour_sentinels": [999],
    "min_booking_h": 0.25,
    "allowed_factors": [1, 2],
    "factor_tolerance": 0.05,
    "max_activities_per_work_order": 2,
    "codes": {
        "activity_type": [
            "Inspect",
            "Replace",
            "Align",
            "Clean",
            "Service",
            "Calibrate",
            "Lubricate",
            "Repair",
            "Adjust",
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
        "detection_method": [
            "scheduled_pm",
            "inspection",
            "operator_report",
            "condition_monitoring",
            "alarm",
        ],
        "outcome": ["Resolved", "Temporary fix"],
    },
    "technician_pattern": "^TECH-[0-9]{2}$",
    "component_to_code": {
        "Whole asset": "GENERAL",
        "Shaft coupling": "COUPLING",
        "Drive-end bearing": "BRG-DE",
        "Bearing and coupling": "BRG-DE",
        "Non-drive-end bearing": "BRG-NDE",
        "Cooling system / heat exchanger": "COOLING-SYS",
        "Lubrication system": "LUBE-SYS",
        "Sensor package": "SENSOR-PKG",
        "Oil filter": "OIL-FILTER",
        "Rotor assembly": "ROTOR",
        "Stator winding": "STATOR-WDG",
        "Mechanical seal": "SEAL-MECH",
        "Impeller": "IMPELLER",
        "Valve assembly": "VALVE",
        "Gear set": "GEAR-SET",
        "Suction strainer / filter": "STRAINER",
        "Fan blade set": "FAN-BLADE",
    },
    "tables": {
        "silver": "silver_maintenance_history",
        "quarantine": "silver_dq_quarantine",
        "audit": "dq_results",
    },
    "rule_meta": {
        "TYPE_CAST": ("Error", "Validity", "Non-empty value that doesn't parse"),
        "DUP_EXACT": ("Drop", "Uniqueness", "Exact duplicate of business columns"),
        "VERSION_LATEST": ("Drop", "Uniqueness", "Superseded version"),
        "SOURCE_KEY_FORMAT": ("Error", "Validity", "source_system_key format"),
        "DOMAIN_CODES": ("Error", "Validity", "Codes in their lists"),
        "COMPONENT_CODE_MAP": ("Error", "Validity", "component to component_code"),
        "WO_NULL_NON_LEGACY": ("Error", "Completeness", "Null work_order_id on CMMS"),
        "RC_NULL_IFF_NONE": ("Error", "Validity", "Root cause null iff NONE"),
        "NDD_IFF_PLANNED": ("Error", "Validity", "Next due date set iff NONE"),
        "EQ_MISSING": ("Row quarantine", "Completeness", "equipment_id null"),
        "TECH_ID_MISSING": ("Field quarantine", "Completeness", "technician_id null"),
        "ACTIVITIES_PER_WO": ("Warn", "Consistency", "More than 2 activities on WO"),
        "TS_END_BEFORE_START": ("Field quarantine", "Validity", "End before start"),
        "TS_AFTER_EXTRACT": ("Warn", "Validity", "Start or end after ingestion_ts"),
        "TS_MODIFIED_BEFORE_END": ("Warn", "Validity", "last_modified_ts < end"),
        "NDD_AFTER_END": ("Warn", "Validity", "Next due date not after end date"),
        "C1": ("Fix", "Validity", "Cost zeroed"),
        "C2": ("Field quarantine", "Validity", "Negative cost nulled"),
        "Q1": ("Fix", "Validity", "Quantity zeroed"),
        "Q2": ("Fix", "Validity", "Quantity made positive"),
        "PARTS_QTY_COST_CONSISTENT": ("Error", "Consistency", "Quantity 0 iff cost 0"),
        "L1": ("Field quarantine", "Validity", "Sentinel 999 nulled"),
        "L2": ("Fix", "Validity", "Minutes converted to hours"),
        "L3": ("Fix", "Validity", "Negative hours made positive"),
        "LABOR_UNEXPLAINED_RATIO": (
            "Error",
            "Validity",
            "Factor not 1 or 2 after fixes",
        ),
    },
}

SOURCE_COLS = [
    "maintenance_id",
    "work_order_id",
    "equipment_id",
    "activity_start_ts",
    "activity_end_ts",
    "activity_type",
    "component",
    "component_code",
    "failure_mode_code",
    "root_cause_code",
    "detection_method",
    "action_description",
    "parts_qty",
    "parts_cost_eur",
    "labor_hours",
    "technician_id",
    "outcome",
    "next_due_date",
    "last_modified_ts",
    "record_version",
    "source_file",
    "ingestion_ts",
    "batch_id",
    "source_system",
    "source_system_key",
]

LINEAGE_COLS = [
    "source_file",
    "ingestion_ts",
    "batch_id",
    "source_system",
    "source_system_key",
]
BUSINESS_COLS = [c for c in SOURCE_COLS if c not in LINEAGE_COLS]

B = lambda e: F.coalesce(e, F.lit(False))
FLAG = lambda n: f"_f_{n}"

# ---------------------------------------------------------------- 2. Core Functions


def clean_maintenance_history(bronze_df: DataFrame, config: dict = CONFIG) -> DataFrame:
    """
    Cleans and standardizes the maintenance history bronze data[cite: 1].
    """

    def _prepare_and_standardise(df: DataFrame) -> DataFrame:
        m = config["bronze_column_map"]
        df = df.select(
            *[
                F.col(f"`{c}`").alias(m.get(c, c))
                for c in df.columns
                if c != "_rescued_data"
            ]
        )
        df = df.withColumn("_source_system", F.col("source_system"))
        df = df.filter(F.col("_load_status") == config["bronze_loaded_status"])

        # Extract JSON for quarantine
        df = df.withColumn(
            "_w_source_json",
            F.to_json(F.struct(*[c for c in df.columns if not c.startswith("_")])),
        )

        # Standardise: Trim strings
        for c, t in df.dtypes:
            if c in SOURCE_COLS and t == "string":
                df = df.withColumn(
                    c, F.when(F.trim(c) == "", None).otherwise(F.trim(c))
                )

        # Track TYPE_CAST failures[cite: 1]
        df = df.withColumn(FLAG("TYPE_CAST"), F.lit(False))

        casts = [
            ("parts_cost_eur", "DECIMAL(12,2)", "try_cast"),
            ("labor_hours", "DECIMAL(8,2)", "try_cast"),
            ("parts_qty", "INT", "try_cast"),
            ("record_version", "INT", "try_cast"),
            ("activity_start_ts", config["timestamp_pattern"], "try_to_timestamp"),
            ("activity_end_ts", config["timestamp_pattern"], "try_to_timestamp"),
            ("last_modified_ts", config["timestamp_pattern"], "try_to_timestamp"),
            ("ingestion_ts", config["timestamp_pattern"], "try_to_timestamp"),
            ("next_due_date", config["date_pattern"], "try_to_date"),
        ]

        for c, fmt, fn in casts:
            fmt = fmt.replace("'", "\\'")  # pattern's 'T'/'Z' quotes break the SQL literal
            expr_str = (
                f"{fn}({c}, '{fmt}')" if fn != "try_cast" else f"{fn}({c} AS {fmt})"
            )
            # Fails TYPE_CAST if originally not null/empty, but casting results in null[cite: 1]
            df = df.withColumn(
                FLAG("TYPE_CAST"),
                F.col(FLAG("TYPE_CAST"))
                | (
                    F.col(c).isNotNull()
                    & (F.trim(F.col(c).cast("string")) != "")
                    & F.expr(expr_str).isNull()
                ),
            )
            df = df.withColumn(c, F.expr(expr_str))

        return df

    def _deduplicate(df: DataFrame) -> DataFrame:
        bhash = F.sha2(
            F.to_json(
                F.struct(*[F.col(c).cast("string") for c in BUSINESS_COLS]),
                {"ignoreNullFields": "false"},
            ),
            256,
        )
        w_exact = Window.partitionBy("_bhash").orderBy(
            "_ingestion_ts", "_source_file", "_record_hash"
        )
        df = df.withColumn("_bhash", bhash).withColumn(
            FLAG("DUP_EXACT"), F.row_number().over(w_exact) > 1
        )

        w_version = Window.partitionBy("maintenance_id", FLAG("DUP_EXACT")).orderBy(
            F.desc_nulls_last("record_version"),
            F.desc_nulls_last("last_modified_ts"),
            F.desc_nulls_last("_ingestion_ts"),
            "_record_hash",
        )
        return df.withColumn(
            FLAG("VERSION_LATEST"),
            ~F.col(FLAG("DUP_EXACT"))
            & F.col("maintenance_id").isNotNull()
            & (F.row_number().over(w_version) > 1),
        )

    def _apply_validation_rules(df: DataFrame) -> DataFrame:
        df = df.withColumn(
            FLAG("SOURCE_KEY_FORMAT"),
            F.col("source_system_key")
            != F.concat_ws("|", F.col("source_system"), F.col("maintenance_id")),
        )

        map_expr = F.create_map(
            *[F.lit(x) for kv in config["component_to_code"].items() for x in kv]
        )
        df = df.withColumn(
            FLAG("COMPONENT_CODE_MAP"),
            F.col("component_code") != map_expr[F.col("component")],
        )

        domain_fail = (
            ~F.col("activity_type").isin(config["codes"]["activity_type"])
            | ~F.col("failure_mode_code").isin(config["codes"]["failure_mode_code"])
            | (
                F.col("root_cause_code").isNotNull()
                & ~F.col("root_cause_code").isin(config["codes"]["root_cause_code"])
            )
            | ~F.col("detection_method").isin(config["codes"]["detection_method"])
            | ~F.col("outcome").isin(config["codes"]["outcome"])
            | (
                F.col("technician_id").isNotNull()
                & ~F.col("technician_id").rlike(config["technician_pattern"])
            )
        )
        df = df.withColumn(FLAG("DOMAIN_CODES"), B(domain_fail))

        df = df.withColumn(
            FLAG("WO_NULL_NON_LEGACY"),
            F.col("work_order_id").isNull()
            & (F.col("source_system") != config["legacy_source_system"]),
        )
        df = df.withColumn(FLAG("EQ_MISSING"), F.col("equipment_id").isNull())
        df = df.withColumn(FLAG("TECH_ID_MISSING"), F.col("technician_id").isNull())
        df = df.withColumn(
            FLAG("RC_NULL_IFF_NONE"),
            F.col("root_cause_code").isNull() & (F.col("failure_mode_code") != "NONE"),
        )
        df = df.withColumn(
            FLAG("NDD_IFF_PLANNED"),
            F.col("next_due_date").isNotNull() & (F.col("failure_mode_code") != "NONE"),
        )

        w_wo = Window.partitionBy("work_order_id")
        df = df.withColumn(
            FLAG("ACTIVITIES_PER_WO"),
            F.col("work_order_id").isNotNull()
            & (
                F.count("maintenance_id").over(w_wo)
                > config["max_activities_per_work_order"]
            ),
        )
        df = df.withColumn(
            FLAG("TS_AFTER_EXTRACT"),
            B(
                (F.col("activity_start_ts") > F.col("ingestion_ts"))
                | (F.col("activity_end_ts") > F.col("ingestion_ts"))
            ),
        )
        df = df.withColumn(
            FLAG("TS_MODIFIED_BEFORE_END"),
            B(F.col("last_modified_ts") < F.col("activity_end_ts")),
        )
        df = df.withColumn(
            FLAG("NDD_AFTER_END"),
            B(
                F.col("next_due_date")
                <= F.coalesce(
                    F.col("activity_end_ts").cast("date"),
                    F.col("activity_start_ts").cast("date"),
                )
            ),
        )

        return df

    def _apply_fixes(df: DataFrame) -> DataFrame:
        df = df.withColumn(
            FLAG("TS_END_BEFORE_START"),
            B(F.col("activity_end_ts") < F.col("activity_start_ts")),
        )
        df = df.withColumn(
            "activity_end_ts",
            F.when(F.col(FLAG("TS_END_BEFORE_START")), None).otherwise(
                F.col("activity_end_ts")
            ),
        )

        c1 = F.col("activity_type").isin(config["no_parts_activity_types"]) & (
            F.col("parts_cost_eur") != 0
        )
        c2 = ~c1 & (F.col("parts_cost_eur") < 0)

        df = df.withColumn(FLAG("C1"), c1).withColumn(FLAG("C2"), c2)
        df = df.withColumn(
            "parts_cost_eur",
            F.when(c1, F.lit(0).cast(DecimalType(12, 2)))
            .when(c2, None)
            .otherwise(F.col("parts_cost_eur")),
        )
        df = df.withColumn(
            "parts_fix_rule",
            F.when(c1, "NO_PARTS_ACTIVITY_COST_ZEROED")
            .when(c2, "NEG_COST_NULLED")
            .otherwise("UNCHANGED"),
        )

        q1 = (F.col("parts_cost_eur") == 0) & (F.col("parts_qty") != 0)
        q2 = (F.col("parts_qty") < 0) & (
            (F.col("parts_cost_eur") != 0) | F.col("parts_cost_eur").isNull()
        )

        df = df.withColumn(FLAG("Q1"), q1).withColumn(FLAG("Q2"), q2)
        df = df.withColumn(
            "parts_qty",
            F.when(q1, F.lit(0).cast(IntegerType()))
            .when(q2, F.abs(F.col("parts_qty")))
            .otherwise(F.col("parts_qty")),
        )
        df = df.withColumn(
            "parts_fix_rule",
            F.when(
                q1,
                F.concat_ws(
                    ";", F.col("parts_fix_rule"), F.lit("ZERO_COST_QTY_ZEROED")
                ),
            )
            .when(
                q2,
                F.concat_ws(";", F.col("parts_fix_rule"), F.lit("NEG_QTY_CORRECTED")),
            )
            .otherwise(F.col("parts_fix_rule")),
        )
        df = df.withColumn(
            "parts_fix_rule", F.expr("replace(parts_fix_rule, 'UNCHANGED;', '')")
        )
        df = df.withColumn(
            FLAG("PARTS_QTY_COST_CONSISTENT"),
            (F.col("parts_cost_eur") == 0) & (F.col("parts_qty") != 0),
        )

        dur_h = F.expr(
            "(unix_timestamp(activity_end_ts) - unix_timestamp(activity_start_ts)) / 3600"
        )
        dur_floor = F.greatest(dur_h, F.lit(config["min_booking_h"]))
        ratio = F.abs(F.col("labor_hours")) / dur_floor
        factor_60 = F.round(ratio / 60)
        valid_factor_60 = factor_60.isin(*config["allowed_factors"]) & (
            F.abs((ratio / 60) - factor_60) <= config["factor_tolerance"]
        )

        l1 = F.col("labor_hours").isin(*config["labour_sentinels"])
        l2 = ~l1 & dur_h.isNotNull() & valid_factor_60
        l3 = ~l1 & ~l2 & (F.col("labor_hours") < 0)

        df = (
            df.withColumn(FLAG("L1"), B(l1))
            .withColumn(FLAG("L2"), B(l2))
            .withColumn(FLAG("L3"), B(l3))
        )
        df = df.withColumn(
            "labor_hours",
            F.when(l1, None)
            .when(l2, F.col("labor_hours") / 60)
            .when(l3, F.abs(F.col("labor_hours")))
            .otherwise(F.col("labor_hours")),
        ).withColumn("labor_hours", F.col("labor_hours").cast(DecimalType(8, 2)))

        df = df.withColumn(
            "labor_fix_rule",
            F.when(l1, "SENTINEL_999_NULLED")
            .when(l2, "MINUTES_TO_HOURS")
            .when(l3, "NEGATIVE_ABS")
            .otherwise("UNCHANGED"),
        )

        ratio_final = F.abs(F.col("labor_hours")) / dur_floor
        df = df.withColumn(
            FLAG("LABOR_UNEXPLAINED_RATIO"),
            F.col("labor_hours").isNotNull()
            & dur_h.isNotNull()
            & ~(
                F.round(ratio_final).isin(*config["allowed_factors"])
                & (
                    F.abs(ratio_final - F.round(ratio_final))
                    <= config["factor_tolerance"]
                )
            ),
        )

        return df

    return _apply_fixes(
        _apply_validation_rules(_deduplicate(_prepare_and_standardise(bronze_df)))
    )


def create_silver_tables(
    cleaned_df: DataFrame,
    catalog: str,
    schema: str,
    run_id: str,
    config: dict = CONFIG,
    spark=None,
) -> None:
    silver_table = f"{catalog}.{schema}.{config['tables']['silver']}"
    quar_table = f"{catalog}.{schema}.{config['tables']['quarantine']}"
    audit_table = f"{catalog}.{schema}.{config['tables']['audit']}"

    # Error Gate checks[cite: 1]
    error_rules = [
        FLAG("TYPE_CAST"),
        FLAG("SOURCE_KEY_FORMAT"),
        FLAG("DOMAIN_CODES"),
        FLAG("COMPONENT_CODE_MAP"),
        FLAG("WO_NULL_NON_LEGACY"),
        FLAG("RC_NULL_IFF_NONE"),
        FLAG("NDD_IFF_PLANNED"),
        FLAG("PARTS_QTY_COST_CONSISTENT"),
        FLAG("LABOR_UNEXPLAINED_RATIO"),
    ]
    errors_found = cleaned_df.filter(F.expr(" OR ".join(error_rules))).count()
    if errors_found > 0:
        # Stop before writing Silver if invariant rules fail[cite: 1]
        raise ValueError(
            f"Error gate failed! {errors_found} violations found across invariant rules."
        )

    base_df = cleaned_df.filter(
        ~F.col(FLAG("DUP_EXACT")) & ~F.col(FLAG("VERSION_LATEST"))
    )
    row_quar_df = base_df.filter(F.col(FLAG("EQ_MISSING")))
    valid_silver = base_df.filter(~F.col(FLAG("EQ_MISSING")))

    hash_cols = [
        c
        for c in valid_silver.columns
        if not c.startswith("_") and c not in LINEAGE_COLS
    ]
    valid_silver = (
        valid_silver.withColumn(
            "_silver_row_hash",
            F.sha2(
                F.concat_ws(
                    "|",
                    *[
                        F.coalesce(F.col(c).cast("string"), F.lit("NULL"))
                        for c in hash_cols
                    ],
                ),
                256,
            ),
        )
        .withColumn("_silver_run_id", F.lit(run_id))
        .withColumn("_silver_processed_ts", F.current_timestamp())
    )

    # Generate Quarantine Payloads[cite: 1]
    q_row = row_quar_df.select(
        F.lit("maintenance_history").alias("entity"),
        F.col("maintenance_id").alias("record_id"),
        F.lit("EQ_MISSING").alias("rule_id"),
        F.lit("ROW").alias("quarantine_type"),
        F.lit(None).cast(StringType()).alias("field_name"),
        F.lit("equipment_id null").alias("reason"),
        F.col("_w_source_json").alias("original_values"),
        F.col("record_version"),
        F.col("_batch_id"),
        F.col("_source_file"),
        F.current_timestamp().alias("quarantine_ts"),
        F.current_timestamp().alias("last_seen_ts"),
        F.lit(run_id).alias("last_run_id"),
        F.lit("OPEN").alias("resolution_status"),
        F.lit(None).cast("timestamp").alias("resolved_ts"),
    )

    q_fields = [
        (FLAG("TECH_ID_MISSING"), "TECH_ID_MISSING", "technician_id"),
        (FLAG("TS_END_BEFORE_START"), "TS_END_BEFORE_START", "activity_end_ts"),
        (FLAG("C2"), "NEG_COST_NULLED", "parts_cost_eur"),
        (FLAG("L1"), "SENTINEL_999_NULLED", "labor_hours"),
    ]

    full_quar_df = q_row
    for flag, rule_id, field in q_fields:
        q_f = valid_silver.filter(F.col(flag)).select(
            F.lit("maintenance_history").alias("entity"),
            F.col("maintenance_id").alias("record_id"),
            F.lit(rule_id).alias("rule_id"),
            F.lit("FIELD").alias("quarantine_type"),
            F.lit(field).alias("field_name"),
            F.lit(f"{field} violation").alias("reason"),
            F.concat(
                F.lit(f'{{"{field}": "'), F.col(field).cast(StringType()), F.lit('"}')
            ).alias("original_values"),
            F.col("record_version"),
            F.col("_batch_id"),
            F.col("_source_file"),
            F.current_timestamp().alias("quarantine_ts"),
            F.current_timestamp().alias("last_seen_ts"),
            F.lit(run_id).alias("last_run_id"),
            F.lit("OPEN").alias("resolution_status"),
            F.lit(None).cast("timestamp").alias("resolved_ts"),
        )
        full_quar_df = full_quar_df.unionByName(q_f)

    # Silver Select
    final_silver_cols = [c for c in SOURCE_COLS if c not in LINEAGE_COLS] + [
        "parts_fix_rule",
        "labor_fix_rule",
        "source_file",
        "ingestion_ts",
        "batch_id",
        "source_system",
        "source_system_key",
        "_ingestion_ts",
        "_batch_id",
        "_source_file",
        "_source_system",
        "_record_hash",
        "_silver_run_id",
        "_silver_processed_ts",
        "_silver_row_hash",
    ]
    valid_silver = valid_silver.select(*final_silver_cols)

    # --- Write Operations: Silver Merge ---[cite: 1]
    if spark.catalog.tableExists(silver_table):
        DeltaTable.forName(spark, silver_table).alias("t").merge(
            valid_silver.alias("s"), "t.maintenance_id = s.maintenance_id"
        ).whenMatchedUpdateAll(
            condition="s.record_version > t.record_version OR s._silver_row_hash <> t._silver_row_hash"
        ).whenNotMatchedInsertAll().whenNotMatchedBySourceDelete().execute()
    else:
        valid_silver.write.format("delta").saveAsTable(silver_table)

    # --- Write Operations: Quarantine Merge ---[cite: 1]
    if spark.catalog.tableExists(quar_table):
        if "resolved_ts" not in spark.table(quar_table).columns:
            spark.sql(f"ALTER TABLE {quar_table} ADD COLUMNS (resolved_ts TIMESTAMP)")
        DeltaTable.forName(spark, quar_table).alias("t").merge(
            full_quar_df.alias("s"),
            "t.entity = s.entity AND t.record_id = s.record_id AND t.rule_id = s.rule_id",
        ).whenMatchedUpdate(
            set={
                "last_seen_ts": "s.last_seen_ts",
                "last_run_id": "s.last_run_id",
                "resolution_status": "'OPEN'",
            }
        ).whenNotMatchedInsertAll().whenNotMatchedBySourceUpdate(
            condition="t.entity = 'maintenance_history' AND t.resolution_status = 'OPEN'",
            set={
                "resolution_status": "'RESOLVED'",
                "resolved_ts": "current_timestamp()",
            },
        ).execute()
    else:
        full_quar_df.write.format("delta").saveAsTable(quar_table)

    # --- Write Operations: DQ Results ---[cite: 1]
    total_bronze = cleaned_df.count()
    exact_dupes = cleaned_df.filter(F.col(FLAG("DUP_EXACT"))).count()
    superseded = cleaned_df.filter(F.col(FLAG("VERSION_LATEST"))).count()

    # base_df is the dataset AFTER deduplication (9,800 rows constraint)
    total_base = base_df.count()
    total_silver = valid_silver.count()
    total_row_quar = row_quar_df.count()
    records_failed_flow = total_bronze - (
        total_silver + exact_dupes + superseded + total_row_quar
    )

    audit_records = [
        {
            "table_name": config["tables"]["silver"],
            "rule_name": "ROW_FLOW:ALL",
            "dimension": "Completeness",
            "severity": "Warn",
            "description": "Every Bronze row has exactly one outcome",
            "records_evaluated": total_bronze,
            "records_failed": records_failed_flow,
            "failure_rate": float(records_failed_flow) / total_bronze
            if total_bronze > 0
            else 0.0,
            "run_ts": datetime.now(UTC),
            "run_id": run_id,
        }
    ]

    rule_flags = [
        c.replace("_f_", "") for c in cleaned_df.columns if c.startswith("_f_")
    ]

    # 1. Count Raw Rules on cleaned_df (10,000 rows)
    raw_rules = ["TYPE_CAST", "DUP_EXACT", "VERSION_LATEST"]
    raw_exprs = [
        F.sum(F.col(f"_f_{rule}").cast("int")).alias(rule)
        for rule in raw_rules
        if rule in rule_flags
    ]
    if raw_exprs:
        raw_counts = cleaned_df.agg(*raw_exprs).collect()[0].asDict()
        for rule_name, count in raw_counts.items():
            sev, dim, desc = config["rule_meta"][rule_name]
            audit_records.append(
                {
                    "table_name": config["tables"]["silver"],
                    "rule_name": rule_name,
                    "dimension": dim,
                    "severity": sev,
                    "description": desc,
                    "records_evaluated": total_bronze,
                    "records_failed": count or 0,
                    "failure_rate": float(count or 0) / total_bronze
                    if total_bronze > 0
                    else 0.0,
                    "run_ts": datetime.now(UTC),
                    "run_id": run_id,
                }
            )

    # 2. Count Business Rules on base_df (9,800 rows after deduplication)
    bus_rules = [
        r for r in rule_flags if r not in raw_rules and r in config["rule_meta"]
    ]
    bus_exprs = [
        F.sum(F.col(f"_f_{rule}").cast("int")).alias(rule) for rule in bus_rules
    ]
    if bus_exprs:
        bus_counts = base_df.agg(*bus_exprs).collect()[0].asDict()
        for rule_name, count in bus_counts.items():
            sev, dim, desc = config["rule_meta"][rule_name]
            audit_records.append(
                {
                    "table_name": config["tables"]["silver"],
                    "rule_name": rule_name,
                    "dimension": dim,
                    "severity": sev,
                    "description": desc,
                    "records_evaluated": total_base,
                    "records_failed": count or 0,
                    "failure_rate": float(count or 0) / total_base
                    if total_base > 0
                    else 0.0,
                    "run_ts": datetime.now(UTC),
                    "run_id": run_id,
                }
            )

    audit_df = spark.createDataFrame(audit_records)
    if spark.catalog.tableExists(audit_table):
        audit_df.write.format("delta").mode("append").saveAsTable(audit_table)
    else:
        audit_df.write.format("delta").saveAsTable(audit_table)


def main() -> None:
    catalog, schema = "data2026acc123456", "team3"
    run_id = f"mh-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"

    bronze_df = spark.table(f"{catalog}.{schema}.bronze_maintenance_history")

    cleaned_df = clean_maintenance_history(bronze_df, CONFIG)
    create_silver_tables(cleaned_df, catalog, schema, run_id, CONFIG, spark)


if __name__ == "__main__":
    main()
