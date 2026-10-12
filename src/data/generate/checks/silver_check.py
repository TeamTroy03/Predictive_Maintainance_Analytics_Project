#!/usr/bin/env python3
"""Run the Silver notebooks' own cleaning code on generated files and report which rules fire.

Reads the code cells of ``.claude/reference/test_pipeline.ipynb`` (Silver equipment, maintenance history, work
orders, sensor events), executes only their pure cleaning parts (everything before each notebook's "main", so
nothing is written) on Bronze-like Spark DataFrames built from the files in ``--raw-dir``, and prints the rules that
fired. Exit code 1 if an ERROR rule fires: in the real notebooks that stops the whole Silver run before any write.

Bronze is emulated as the notebook does it: source columns plus ``record_hash``, ``ingestion_timestamp``,
``source_filename``, ``batch_id`` (the file's position in the chain) and ``load_status``; the source's own
``batch_id`` becomes ``source_batch_id``. Types come from Spark's inference (Auto Loader infers types too).

Usage (not part of CI; needs Java 8/11/17 and PySpark 3.5 on Python <= 3.12)::

    python src/data/generate/checks/silver_check.py              # baseline + every increment in src/data/raw/chain.json
    python src/data/generate/checks/silver_check.py --tags 2     # the baseline only (control)
    python src/data/generate/checks/silver_check.py --raw-dir <dir>   # another folder with chain.json and the files

Windows: Spark's launcher breaks on non-ASCII paths (e.g. a user profile with Greek letters). Use an environment
and temp folder with ASCII-only paths::

    uv venv C:\\spark_env --python 3.12 && uv pip install --python C:\\spark_env\\Scripts\\python.exe "pyspark==3.5.*"
    set TEMP=C:\\spark_tmp & set TMP=C:\\spark_tmp          (the folder must exist)
    C:\\spark_env\\Scripts\\python.exe src/data/generate/checks/silver_check.py
"""
from __future__ import annotations

import argparse
import json
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parents[4]  # checks -> generate -> data -> src -> repository root
DEFAULT_NOTEBOOK = REPO / ".claude" / "reference" / "test_pipeline.ipynb"
DEFAULT_RAW_DIR = REPO / "src" / "data" / "raw"
ACTIVITY = ("work_orders", "maintenance_history", "sensor_events")
EXT = {"equipment": "csv", "work_orders": "csv", "maintenance_history": "csv", "sensor_events": "json"}


# --- notebook loading -----------------------------------------------------------------------------------------
def load_cells(path: Path) -> list[str]:
    nb = json.loads(path.read_text(encoding="utf-8"))
    return ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]


def find_cell(cells: list[str], marker: str) -> str:
    for src in cells:
        if marker in src:
            return src
    raise SystemExit(f"notebook layout changed: no code cell contains {marker!r}")


def cut_before(src: str, marker: str) -> str:
    """Everything before the line that starts the notebook's main section."""
    index = src.find(marker)
    if index < 0:
        raise SystemExit(f"notebook layout changed: marker {marker!r} not found")
    return src[:index]


def make_namespace(spark, setup_cells: list[str], code: str) -> dict:
    """Fresh namespace: the notebook's import and config cells, then the cell's pure cleaning code."""
    module = types.ModuleType(f"silver_ns_{len(sys.modules)}")  # @dataclass looks its module up in sys.modules
    sys.modules[module.__name__] = module
    ns = module.__dict__
    ns["spark"] = spark
    for cell in setup_cells:
        exec(cell, ns)  # noqa: S102 - the notebook is a trusted project file
    exec(code, ns)  # noqa: S102
    return ns


def register_spark4_functions(spark) -> None:
    """Spark 3.5 (the newest that runs on Java 8) lacks ``try_to_date``, which Databricks has: the maintenance
    notebook calls it as ``try_to_date(next_due_date, 'yyyy-MM-dd')``. Same semantics: null if it does not parse."""
    from datetime import date, datetime

    from pyspark.sql.types import DateType

    def try_to_date(value, pattern):
        if value is None:
            return None
        if isinstance(value, date):
            return value if not isinstance(value, datetime) else value.date()
        assert pattern == "yyyy-MM-dd", f"harness shim only knows yyyy-MM-dd, got {pattern}"
        try:
            return date.fromisoformat(str(value).strip())
        except ValueError:
            return None

    spark.udf.register("try_to_date", try_to_date, DateType())


# --- Bronze emulation -------------------------------------------------------------------------------------------
def bronze_frame(spark, F, paths: list[Path], dataset: str):
    """Bronze-like DataFrame of all files of one dataset (``paths`` in chain order)."""
    path_strings = [p.as_posix() for p in paths]
    if dataset == "sensor_events":
        raw = (spark.read.option("multiLine", "true").option("primitivesAsString", "true")
               .json(path_strings))
    else:
        raw = spark.read.option("header", "true").option("inferSchema", "true").csv(path_strings)
    raw = raw.withColumn("_bronze_file_path", F.input_file_name()).withColumn(
        "_rescued_data", F.lit(None).cast("string"))
    payload = sorted(c for c in raw.columns if c != "_bronze_file_path")
    df = raw.withColumn("record_hash", F.sha2(F.to_json(F.struct(*[F.col(f"`{c}`") for c in payload]),
                                                        options={"ignoreNullFields": "false"}), 256))
    if "batch_id" in df.columns:
        df = df.withColumnRenamed("batch_id", "source_batch_id")
    order = {p.name: i for i, p in enumerate(paths)}
    name = F.regexp_extract("_bronze_file_path", r"([^/]+)$", 1)
    position = F.lit(None).cast("long")
    for file_name, i in order.items():
        position = F.when(name == file_name, F.lit(i)).otherwise(position)
    return (df.withColumn("_position", position)
            .withColumn("ingestion_timestamp", F.expr("timestamp'2026-10-01 00:00:00'") + F.expr("make_interval(0,0,0,_position)"))
            .withColumn("source_filename", F.col("_bronze_file_path"))
            .withColumn("batch_id", F.col("_position"))
            .withColumn("load_status", F.when(F.col("_rescued_data").isNotNull(), F.lit("RESCUED")).otherwise(F.lit("SUCCESS")))
            .drop("_bronze_file_path", "_position"))


# --- per dataset runs ---------------------------------------------------------------------------------------------
def run_equipment(spark, cells, setup, F, files):
    ns = make_namespace(spark, setup, cut_before(find_cell(cells, "def check_power_limits"),
                                                       "# Step 10: Orchestration"))
    df = bronze_frame(spark, F, files, "equipment")
    for step in ("check_contracts", "standardise", "flag_duplicates", "check_equipment_dates"):
        df = ns[step](df)
    df = ns["check_power_limits"](df, ns["MAX_POWER_KW"])
    for step in ("check_vibration_limits", "check_missing_keys", "check_rules"):
        df = ns[step](df)
    valid, quarantine, _ = ns["build_silver"](df)
    reasons = (quarantine.select(F.explode(F.split("quarantine_reason", r" \| ")).alias("rule"))
               .groupBy("rule").count().collect())
    return valid, {"rows": df.count(), "silver": valid.count(), "quarantined": quarantine.count()}, \
        {r["rule"]: r["count"] for r in reasons}, {}


def run_work_orders(spark, cells, setup, F, files):
    ns = make_namespace(spark, setup, cut_before(find_cell(cells, "def clean_worker_ord"),
                                                       "# ======== 4. main"))
    cleaned, flagged = ns["clean_worker_ord"](bronze_frame(spark, F, files, "work_orders"))
    cleaned = ns["add_row_quarantine"](cleaned)
    stats = ns["collect_stats"](cleaned, flagged)["ALL"]
    fired, errors = {}, {}
    for rule, (_, severity, _) in ns["RULES"].items():
        if stats.get(rule):
            fired[f"{rule} [{severity}]"] = stats[rule]
            if severity == "ERROR":
                errors[rule] = stats[rule]
    counts = {"rows": stats["bronze"], "silver": stats["after_dedup"] - stats["row_quarantine"],
              "row_quarantine": stats["row_quarantine"]}
    return cleaned, counts, fired, errors


def run_maintenance(spark, cells, setup, F, files):
    ns = make_namespace(spark, setup, cut_before(find_cell(cells, "def clean_maintenance_history"),
                                                       'run_id = f"mh-'))
    cleaned = ns["clean_maintenance_history"](bronze_frame(spark, F, files, "maintenance_history"), ns["CONFIG"])
    flag_columns = sorted(c for c in cleaned.columns if c.startswith("_f_"))
    totals = cleaned.agg(*[F.sum(F.col(c).cast("int")).alias(c) for c in flag_columns]).first().asDict()
    meta = ns["CONFIG"]["rule_meta"]
    fired, errors = {}, {}
    for column, count in totals.items():
        rule = column.removeprefix("_f_")
        if count:
            severity = meta.get(rule, ("?",))[0]
            fired[f"{rule} [{severity}]"] = count
            if severity == "Error":
                errors[rule] = count
    kept = cleaned.filter(~F.col("_f_DUP_EXACT") & ~F.col("_f_VERSION_LATEST"))
    counts = {"rows": cleaned.count(), "after_dedup": kept.count()}
    return cleaned, counts, fired, errors


def run_sensors(spark, cells, setup, F, files, silver_equipment):
    ns = make_namespace(spark, setup, cut_before(find_cell(cells, "def clean_bronze_table"),
                                                       '"""Run in Databricks with an active Spark session."""'))
    result = ns["clean_bronze_table"](bronze_frame(spark, F, files, "sensor_events"), silver_equipment)
    rejected = (result.quarantine.groupBy("rule_id").count().collect()
                if "rule_id" in result.quarantine.columns else [])
    fired = {f"{r['rule_id']} [reject]": r["count"] for r in rejected}
    flags = {c: result.silver.filter(F.col(c)).count() for c in result.silver.columns
             if c in ("dq_unknown_equipment",)}
    fired.update({f"{k} [flag]": v for k, v in flags.items() if v})
    return result.silver, {k: v for k, v in result.counts.items() if isinstance(v, int)}, fired, {}


# --- main --------------------------------------------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--tags", help="comma list of chain tags to include (default: baseline and all increments)")
    parser.add_argument("--notebook", type=Path, default=DEFAULT_NOTEBOOK)
    args = parser.parse_args()

    chain = json.loads((args.raw_dir / "chain.json").read_text(encoding="utf-8"))
    all_tags = [chain["baseline"]["tag"]] + [i["tag"] for i in chain.get("increments", [])]
    tags = args.tags.split(",") if args.tags else all_tags
    unknown = [t for t in tags if t not in all_tags]
    if unknown:
        raise SystemExit(f"tags not in chain.json: {unknown}")

    def files(dataset: str) -> list[Path]:
        return [args.raw_dir / f"{dataset}_{t}.{EXT[dataset]}" for t in tags if (t == all_tags[0] or dataset in ACTIVITY)]

    sys.modules.setdefault("delta", types.ModuleType("delta"))  # notebooks import DeltaTable; only used by writes
    tables = types.ModuleType("delta.tables")
    tables.DeltaTable = object
    sys.modules["delta.tables"] = tables
    try:
        from pyspark.sql import SparkSession
        from pyspark.sql import functions as F
    except ImportError:
        raise SystemExit("pyspark is not installed: see the usage notes at the top of this file") from None
    spark = (SparkSession.builder.master("local[2]").appName("silver_check").config("spark.ui.enabled", "false")
             .config("spark.sql.session.timeZone", "UTC").config("spark.driver.host", "127.0.0.1")
             .config("spark.sql.shuffle.partitions", "4").getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")

    cells = load_cells(args.notebook)
    setup = [find_cell(cells, "from delta.tables import DeltaTable"), find_cell(cells, "BASE_SOURCE_PATH")]
    register_spark4_functions(spark)
    print(f"Silver check of chain tags {tags} from {args.raw_dir}\n")
    exit_code = 0
    results = {}
    runs = []
    if all_tags[0] in tags:
        runs.append(("equipment", lambda: run_equipment(spark, cells, setup, F, files("equipment"))))
    runs += [("work_orders", lambda: run_work_orders(spark, cells, setup, F, files("work_orders"))),
             ("maintenance_history", lambda: run_maintenance(spark, cells, setup, F, files("maintenance_history")))]
    for name, run in runs:
        try:
            frame, counts, fired, errors = run()
        except Exception as exc:  # noqa: BLE001 - the notebook code itself failed: report and keep going
            print(f"== {name}: FAILED to run: {type(exc).__name__}: {str(exc)[:300]}\n")
            exit_code = 1
            continue
        results[name] = frame
        print(f"== {name}: {counts}")
        for rule, count in sorted(fired.items()):
            print(f"     {rule:<46}{count:>8,}")
        if errors:
            exit_code = 1
            print(f"   ERROR RULES FIRED (the real Silver run would stop): {errors}")
        print()
    if "equipment" in results:
        try:
            frame, counts, fired, errors = run_sensors(spark, cells, setup, F, files("sensor_events"),
                                                       results["equipment"])
            print(f"== sensor_events: {counts}")
            for rule, count in sorted(fired.items()):
                print(f"     {rule:<46}{count:>8,}")
            print()
        except Exception as exc:  # noqa: BLE001 - report the failure and keep going
            print(f"== sensor_events: FAILED to run: {type(exc).__name__}: {str(exc)[:300]}\n")
            exit_code = 1
    else:
        print("== sensor_events: skipped (needs the Silver equipment table: include the baseline tag)\n")
    print("RESULT:", "no ERROR rule fired" if exit_code == 0 else "FAILURES (see above)")
    spark.stop()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
