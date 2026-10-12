# Bronze ingestion

**Spec:** §6.1 Databricks: Bronze Ingestion

**Acceptance condition:** the team can trace any downstream record to its source file and
ingestion batch and can rerun a failed batch without creating uncontrolled duplicates.

## Must implement

- Parameterized ingestion for CSV (`equipment`, `maintenance_history`, `work_orders`) and JSON (`sensor_events`)
- Persist as Bronze Delta tables with **no business-level transformations**
- Add ingestion timestamp, batch ID, source filename, record hash, load status
- Schema validation and a controlled response to schema drift
- Idempotent reruns + incremental load (or a justified full-refresh pattern, recorded in `docs/adr/`)
- Log input, written and rejected counts, duration and failure details

## Implementation (`bronze_pipeline.ipynb`)

- **Sources:** `/Volumes/data2026acc123456/team3/datasets/{equipment,maintenance_history,work_orders,sensor_events}/`, one
  Auto Loader stream per dataset (`availableNow`, checkpoints under `.../team3/checkpoints/`, one folder per stream). CSV uses
  `cloudFiles` with type inference, schema evolution `addNewColumns` and a rescued-data column; the sensor JSON arrays are read
  as binary files and exploded with `from_json` (`FAILFAST`, all primitives as strings).
- **Added columns:** `record_hash` (SHA-256 of the whole source row), `ingestion_timestamp`, `source_filename`, `batch_id`
  (the stream's micro-batch id; the source's own `batch_id` is kept as `source_batch_id`) and `load_status`
  (`SUCCESS`, or `RESCUED` when the rescued-data column is filled).
- **Idempotent reruns:** the first run creates the table, later runs `MERGE ... ON record_hash WHEN NOT MATCHED INSERT`, so a
  file that is loaded again adds no duplicates. Nothing is ever deleted: removing a file from the volume does not remove its
  rows from Bronze.
- **Audit:** one row per micro-batch in `bronze_ingestion_audit` (input and written counts, duration, status, error).
  `rejected_count` is always 0: rows are never rejected in Bronze, problems are flagged with `load_status`.

