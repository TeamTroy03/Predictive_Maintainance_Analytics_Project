import uuid
from datetime import UTC, datetime
from decimal import Decimal

from pyspark.sql import DataFrame, Window
from pyspark.sql import functions as F


# ---------------------------------------------------------------- 1. Config & Setup
class Config:
    bronze_loaded_status = "SUCCESS"
    bronze_column_map = {
        "batch_id": "_batch_id",
        "source_batch_id": "batch_id",
        "ingestion_timestamp": "_ingestion_ts",
        "source_filename": "_source_file",
        "record_hash": "_record_hash",
        "load_status": "_load_status",
    }
    timestamp_pattern = "yyyy-MM-dd'T'HH:mm:ss'Z'"
    standard_labour_rate_eur_h = Decimal(55)
    labour_sentinels = [Decimal(9999)]
    labour_match_tolerance_h = Decimal("0.01")


CFG = Config()
RUN_ID = f"wo-{datetime.now(UTC):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:6]}"

# ---------------------------------------------------------------- 2. Columns & Expressions
SOURCE_COLS = [
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
    "downtime_start_ts",
    "downtime_end_ts",
    "downtime_minutes",
    "downtime_type",
    "closed_ts",
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
    "source_file",
    "ingestion_ts",
    "batch_id",
    "source_system",
    "source_system_key",
]

LINEAGE = [
    "source_file",
    "ingestion_ts",
    "batch_id",
    "source_system",
    "source_system_key",
]
BUSINESS = [c for c in SOURCE_COLS if c not in LINEAGE]
TS = [
    "created_ts",
    "due_ts",
    "actual_start_ts",
    "downtime_start_ts",
    "downtime_end_ts",
    "closed_ts",
    "last_modified_ts",
    "ingestion_ts",
]
CASTS = {
    "labor_hours": "DECIMAL(8,2)",
    "labor_cost": "DECIMAL(12,2)",
    "parts_cost": "DECIMAL(12,2)",
    "downtime_minutes": "INT",
    "record_version": "INT",
}
MONEY = ["labor_cost", "parts_cost", "currency", "last_modified_ts", "record_version"]
FIX_COLS = [
    "ts_fix_rule",
    "labor_fix_rule",
    "parts_fix_rule",
    "equipment_resolution",
    "dq_unknown_equipment",
    "dq_downtime_overlap",
]

SILVER_COLS = [c for c in SOURCE_COLS if c not in LINEAGE + MONEY] + [
    "labor_cost",
    "parts_cost",
    "currency",
    "fx_rate_to_eur",
    "labor_cost_eur",
    "parts_cost_eur",
    "total_cost_eur",
    "last_modified_ts",
    "record_version",
    *FIX_COLS,
    *LINEAGE,
    "_ingestion_ts",
    "_batch_id",
    "_source_file",
    "_source_system",
    "_record_hash",
]

B = lambda e: F.coalesce(e, F.lit(False))
FLAG = lambda n: f"_f_{n}"
fired = lambda pairs: F.filter(
    F.array(*[F.when(c, F.lit(n)) for n, c in pairs]), lambda x: x.isNotNull()
)


# ---------------------------------------------------------------- 3. Core Functions


def silver_cleaning_maintenance(
    bronze: DataFrame,
    equipment: DataFrame,
    fx: DataFrame,
    mh: DataFrame | None = None,
) -> DataFrame:
    """
    Function 1: Applies all business rules, standardization, deduplication, and cleaning logic.
    """

    def _read_and_standardise(df: DataFrame) -> DataFrame:
        m = CFG.bronze_column_map
        df = df.select(
            *[
                F.col(f"`{c}`").alias(m.get(c, c))
                for c in df.columns
                if c != "_rescued_data"
            ]
        )
        df = df.withColumn("_source_system", F.col("source_system"))
        df = df.filter(F.col("_load_status") == CFG.bronze_loaded_status)

        types = dict(df.dtypes)
        df = df.select(
            *[
                F.when(F.trim(c) == "", None).otherwise(F.trim(c)).alias(c)
                if c in SOURCE_COLS and t == "string"
                else F.col(f"`{c}`")
                for c, t in df.dtypes
            ]
        )
        df = df.withColumn("equipment_id", F.upper("equipment_id"))

        for c in TS + list(CASTS):
            new = (
                F.col(c)
                if types.get(c) == "timestamp"
                else F.expr(f'try_to_timestamp({c}, "{CFG.timestamp_pattern}")')
                if c in TS
                else F.expr(f"try_cast({c} AS {CASTS[c]})")
            )
            df = df.withColumn(c, new)
        return df

    def _deduplicate(df: DataFrame) -> DataFrame:
        bhash = F.sha2(
            F.to_json(
                F.struct(*[F.col(c).cast("string") for c in BUSINESS]),
                {"ignoreNullFields": "false"},
            ),
            256,
        )
        w1 = Window.partitionBy("_bhash").orderBy(
            "_ingestion_ts", "_source_file", "_record_hash"
        )
        df = df.withColumn("_bhash", bhash).withColumn(
            FLAG("DUP_EXACT"), F.row_number().over(w1) > 1
        )

        w2 = Window.partitionBy("work_order_id", FLAG("DUP_EXACT")).orderBy(
            F.desc_nulls_last("record_version"),
            F.desc_nulls_last("last_modified_ts"),
            F.desc_nulls_last("_ingestion_ts"),
            "_record_hash",
        )
        return df.withColumn(
            FLAG("VERSION_LATEST"),
            ~F.col(FLAG("DUP_EXACT"))
            & F.col("work_order_id").isNotNull()
            & (F.row_number().over(w2) > 1),
        )

    def _fix_timestamps(df: DataFrame) -> DataFrame:
        lm = F.col("last_modified_ts")
        bad = B(F.col("closed_ts") < F.col("created_ts"))
        guard = (
            (F.col("record_version") == 1)
            & B(lm >= F.col("created_ts"))
            & F.coalesce(lm >= F.col("actual_start_ts"), F.lit(True))
            & F.coalesce(lm >= F.col("downtime_end_ts"), F.lit(True))
        )
        return df.withColumn(
            "closed_ts",
            F.when(bad & guard, lm).when(bad, None).otherwise(F.col("closed_ts")),
        ).withColumn(
            "ts_fix_rule",
            F.when(bad & guard, "CLOSED_TS_FROM_LAST_MODIFIED").otherwise("UNCHANGED"),
        )

    def _convert_and_fix_amounts(df: DataFrame) -> DataFrame:
        fx_sel = fx.select(
            F.col("currency").alias("_fx_ccy"),
            F.col("rate_month").alias("_fx_month"),
            F.col("rate_to_eur").cast("decimal(8,4)").alias("_fx_rate"),
        )
        df = df.join(
            F.broadcast(fx_sel),
            (F.col("currency") == F.col("_fx_ccy"))
            & (F.date_format("created_ts", "yyyy-MM") == F.col("_fx_month")),
            "left",
        ).withColumn(
            "fx_rate_to_eur",
            F.when(F.col("currency") == "EUR", F.lit(1).cast("decimal(8,4)")).otherwise(
                F.col("_fx_rate")
            ),
        )

        fxr, rate = (
            F.col("fx_rate_to_eur"),
            F.lit(CFG.standard_labour_rate_eur_h).cast("decimal(8,2)"),
        )
        lh, lc = F.col("labor_hours"), F.col("labor_cost")
        exp_h = F.round(lc * fxr / rate, 2)

        l1 = B(lh.isin(*CFG.labour_sentinels)) & B(lc > 0) & fxr.isNotNull()
        l2 = (
            B(lh < 0)
            & B(lc > 0)
            & fxr.isNotNull()
            & B(F.abs(F.abs(lh) - exp_h) <= F.lit(CFG.labour_match_tolerance_h))
        )
        lc1 = B(F.col("labor_cost") < 0) & B(F.col("labor_hours") > 0) & fxr.isNotNull()
        p1 = B(F.col("parts_cost") < 0)

        df = (
            df.withColumn(
                "labor_hours",
                F.when(l1, exp_h)
                .when(l2, F.abs(lh))
                .when(
                    (B(lh.isin(*CFG.labour_sentinels)) | B(lh < 0)) & ~(l1 | l2), None
                )
                .otherwise(lh)
                .cast("decimal(8,2)"),
            )
            .withColumn(
                "labor_cost",
                F.when(lc1, F.round(F.col("labor_hours") * rate / fxr, 2))
                .otherwise(F.col("labor_cost"))
                .cast("decimal(12,2)"),
            )
            .withColumn("parts_cost", F.when(p1, None).otherwise(F.col("parts_cost")))
            .withColumn(
                "labor_cost_eur",
                F.round(F.col("labor_cost") * fxr, 2).cast("decimal(12,2)"),
            )
            .withColumn(
                "parts_cost_eur",
                F.round(F.col("parts_cost") * fxr, 2).cast("decimal(12,2)"),
            )
            .withColumn(
                "parts_fix_rule", F.when(p1, "NEG_PARTS_NULLED").otherwise("UNCHANGED")
            )
        )

        rules = fired(
            [
                ("SENTINEL_RECOVERED_FROM_COST", l1),
                ("NEGATIVE_ABS_VERIFIED_BY_COST", l2),
                ("NEG_COST_RECOVERED_FROM_HOURS", lc1),
            ]
        )
        return df.withColumn(
            "labor_fix_rule",
            F.when(F.size(rules) > 0, F.concat_ws(";", rules)).otherwise("UNCHANGED"),
        )

    def _recover_and_finalize(df: DataFrame) -> DataFrame:
        if mh is not None:
            agg = mh.groupBy("work_order_id").agg(
                F.array_distinct(
                    F.collect_list(
                        F.when(
                            F.col("equipment_resolution") == "SOURCE",
                            F.upper(F.trim("equipment_id")),
                        )
                    )
                ).alias("_mh_eq"),
                F.count(F.lit(1)).alias("_mh_n"),
                F.sum(F.col("parts_cost_eur").isNull().cast("int")).alias("_mh_pnull"),
                F.sum("parts_cost_eur").alias("_mh_parts"),
            )
            df = df.join(agg, "work_order_id", "left")
            eq = F.col("equipment_id").isNull() & B(F.size("_mh_eq") == 1)
            p2 = (
                B(F.col("parts_cost") < 0)
                & B(F.col("_mh_n") >= 1)
                & B(F.col("_mh_pnull") == 0)
                & B(F.col("_mh_parts") >= 0)
            )

            df = (
                df.withColumn(FLAG("EQ_RECOVERED"), eq)
                .withColumn(
                    "equipment_id",
                    F.when(eq, F.element_at("_mh_eq", 1)).otherwise(
                        F.col("equipment_id")
                    ),
                )
                .withColumn(
                    "parts_cost_eur",
                    F.when(p2, F.col("_mh_parts").cast("decimal(12,2)")).otherwise(
                        F.col("parts_cost_eur")
                    ),
                )
                .withColumn(
                    "parts_fix_rule",
                    F.when(
                        p2, "NEG_PARTS_NULLED;NEG_PARTS_RECOVERED_FROM_MH"
                    ).otherwise(F.col("parts_fix_rule")),
                )
            )
        else:
            df = df.withColumn(FLAG("EQ_RECOVERED"), F.lit(False))

        master = equipment.select(
            F.upper(F.trim("equipment_id")).alias("_eq_master")
        ).distinct()
        df = df.join(
            F.broadcast(master), F.col("equipment_id") == F.col("_eq_master"), "left"
        )

        eq, s, e = (
            F.col("equipment_id"),
            F.col("downtime_start_ts"),
            F.col("downtime_end_ts"),
        )
        unknown = eq.isNotNull() & F.col("_eq_master").isNull()
        w = (
            Window.partitionBy("equipment_id")
            .orderBy("downtime_start_ts", "work_order_id")
            .rowsBetween(Window.unboundedPreceding, -1)
        )

        return (
            df.withColumn("dq_unknown_equipment", unknown)
            .withColumn(
                "equipment_resolution",
                F.when(F.col(FLAG("EQ_RECOVERED")), "RECOVERED_FROM_MH")
                .when(eq.isNull(), "MISSING")
                .when(unknown, "UNREGISTERED")
                .otherwise("SOURCE"),
            )
            .withColumn(
                "dq_downtime_overlap",
                (eq.isNotNull() & s.isNotNull() & e.isNotNull())
                & B(s < F.max(F.when(s.isNotNull() & e.isNotNull(), e)).over(w)),
            )
            .withColumn(
                "total_cost_eur",
                (F.col("labor_cost_eur") + F.col("parts_cost_eur")).cast(
                    "decimal(12,2)"
                ),
            )
        )

    # Execute the cleaning pipeline
    raw = _deduplicate(_read_and_standardise(bronze))
    kept = raw.filter(~F.col(FLAG("DUP_EXACT")) & ~F.col(FLAG("VERSION_LATEST")))
    cleaned_df = _recover_and_finalize(_convert_and_fix_amounts(_fix_timestamps(kept)))

    return cleaned_df


def silver_table_configuration(cleaned_df: DataFrame, table_name: str) -> None:
    """
    Function 2: Formats the cleaned DataFrame, generates row hashes, and creates the Delta table.
    """
    # 1. Filter out fundamentally invalid rows
    valid_df = cleaned_df.filter(
        F.col("work_order_id").isNotNull() & F.col("created_ts").isNotNull()
    )

    # 2. Generate row hash on business columns
    hash_cols = [c for c in SILVER_COLS if c not in LINEAGE and not c.startswith("_")]
    hashed_df = valid_df.withColumn(
        "_silver_row_hash",
        F.sha2(
            F.concat_ws(
                "|",
                *[
                    F.coalesce(F.col(c).cast("string"), F.lit("§NULL§"))
                    for c in hash_cols
                ],
            ),
            256,
        ),
    )

    # 3. Project to final schema
    final_silver_df = (
        hashed_df.withColumn("_silver_run_id", F.lit(RUN_ID))
        .withColumn("_silver_processed_ts", F.current_timestamp())
        .select(
            *SILVER_COLS, "_silver_run_id", "_silver_processed_ts", "_silver_row_hash"
        )
    )

    # 4. Write to Delta (Overwrite for pure table creation without MERGE complexity)
    print(f"Writing {final_silver_df.count()} records to {table_name}...")
    final_silver_df.write.format("delta").mode("overwrite").option(
        "overwriteSchema", "true"
    ).saveAsTable(table_name)

    print(f"Table {table_name} successfully created/updated.")


# ---------------------------------------------------------------- 4. Main Execution
def main():
    """
    Orchestrates the reading of raw data, the cleaning process, and the silver table creation.
    """
    # Retrieve Source Tables (Assuming PySpark context `spark` is available in the environment)
    bronze_df = spark.table("bronze_work_orders")
    eq_df = spark.table("silver_equipment")
    fx_df = spark.table("ref_fx_rate_monthly")

    # Optionally load maintenance history if it exists for recovery
    try:
        mh_df = spark.table("silver_maintenance_history")
    except Exception:
        mh_df = None
        print("Maintenance history table not found. Skipping recovery from MH.")

    # Execute Function 1: Clean the data
    cleaned_data = silver_cleaning_maintenance(
        bronze=bronze_df, equipment=eq_df, fx=fx_df, mh=mh_df
    )

    # Execute Function 2: Convert to Silver and save
    silver_table_configuration(cleaned_df=cleaned_data, table_name="silver_work_orders")


if __name__ == "__main__":
    main()
