# Databricks

**Spec:** §6.1–6.3 · **Deliverable:** Databricks implementation → `docs/databricks_implementation.md`

| Folder | Spec | Purpose |
|---|---|---|
| `common/` | §6.1–6.3 | Shared audit columns, record hash, run logging, config |
| `bronze/` | §6.1 | Raw, append-only Delta tables with ingestion metadata |
| `silver/` | §6.2 | Validated, standardized, deduplicated, conformed tables + quarantine |
| `golden/` | §6.3 | Star schema: facts, dimensions, aggregates |
| `jobs/` | §6.1, §7 | Job configuration for Bronze → Silver → Gold |

## What exists today

| Folder | Contents |
|---|---|
| `common/` | empty (README only): audit/lineage helpers are not shared yet, each notebook has its own copy |
| `bronze/` | `bronze_pipeline.ipynb`: Auto Loader into `bronze_equipment`, `bronze_maintenance_history`, `bronze_work_orders`, `bronze_sensor_events` + `bronze_ingestion_audit` |
| `silver/` | one script per dataset: `silver-alex/silver_equipment.py`, `silver-max/silver_maintenance.py`, `silver-stau/silver-stau.py` (work orders), `silver-deppy/silver_sensor_event.py` and `silver_sensor_events.ipynb` (sensor events) |
| `golden/` | `fact_sensor_reading.sql`, a two-line placeholder; the Gold tables the API and agent read come from a notebook that is not in this folder yet (see `golden/README.md`) |
| `jobs/` | empty (README only) |

Source files: Bronze reads `/Volumes/data2026acc123456/team3/datasets/<dataset>/` (catalog `data2026acc123456`, schema
`team3`). They are produced and uploaded by `src/data/`; new data continues the delivered plant, see `src/data/README.md`.
`.claude/reference/test_pipeline.ipynb` is a combined Bronze + Silver notebook for the `test_datasets` volume folder that
writes `test_*` tables.

**Silver stops on ERROR rules.** The work-order and maintenance Silver notebooks abort the whole run, before any write, when an
ERROR rule fires. The generated data is checked against them with `src/data/generate/checks/silver_check.py`. The maintenance
rules are specified in `context/silver_max/silver_maintenance_rules.md`.

> This folder is intentionally **not** a Python package (no `__init__.py` here) so it does not
> shadow the `databricks` namespace used by `databricks-sdk` / Databricks Connect.
> See `docs/adr/`.
