# Bronze ingestion

**Spec:** §6.1 Databricks: Bronze Ingestion · **Owner:** @participant1

**Acceptance condition:** the team can trace any downstream record to its source file and
ingestion batch and can rerun a failed batch without creating uncontrolled duplicates.

## Must implement

- Parameterized ingestion for CSV (`equipment`, `maintenance_history`, `work_orders`) and JSON (`sensor_events`)
- Persist as Bronze Delta tables with **no business-level transformations**
- Add ingestion timestamp, batch ID, source filename, record hash, load status
- Schema validation and a controlled response to schema drift
- Idempotent reruns + incremental load (or a justified full-refresh pattern, recorded in `docs/adr/`)
- Log input, written and rejected counts, duration and failure details
