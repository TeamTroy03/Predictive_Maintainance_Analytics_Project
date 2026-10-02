# Data dictionary

**Spec:** §4 "A data dictionary describing fields, types, keys, business meaning and sensitivity."

One table per source file (and later per Silver/Gold table).

## `equipment.csv`
| Field | Type | Key | Business meaning | Sensitivity |
|---|---|---|---|---|
| | | | | |

## `maintenance_history.csv`
| Field | Type | Key | Business meaning | Sensitivity |
|---|---|---|---|---|
| | | | | |

## `work_orders.csv`
| Field | Type | Key | Business meaning | Sensitivity |
|---|---|---|---|---|
| | | | | |

## `sensor_events.json`
| Field (JSON path) | Type | Key | Business meaning | Sensitivity |
|---|---|---|---|---|
| | | | | |

## Audit / lineage columns (all Bronze tables)
| Field | Type | Meaning |
|---|---|---|
| `_ingestion_ts` | timestamp | When the record was ingested |
| `_batch_id` | string | Ingestion batch |
| `_source_file` | string | Source filename |
| `_source_system` | string | Source-system key |
| `_record_hash` | string | Hash of source record for dedup/idempotency |
| `_load_status` | string | Load outcome |
