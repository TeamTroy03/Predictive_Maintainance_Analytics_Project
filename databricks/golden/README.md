# Gold dimensional model

**Spec:** §6.3 Databricks: Gold Dimensional Model (Delta)
**Docs:** `docs/dimensional_model.md`

**Acceptance condition:** business measures can be reproduced from documented facts and
dimensions without embedding inconsistent logic in individual report visuals.

## Team 3 model

| Type | Tables |
|---|---|
| Facts | `fact_sensor_reading` (one reading per equipment per timestamp), `fact_maintenance_work_order` (one work order), `fact_downtime` |
| Dimensions | `dim_date`, `dim_equipment`, `dim_site`, `dim_equipment_type`, `dim_failure_mode`, `dim_maintenance_type` |

## Must implement

- Stated grain for every fact; conformed dimensions
- Surrogate keys and documented unknown-member handling
- Measures/aggregates for: availability, unplanned downtime, MTBF, MTTR, overdue work orders,
  alarm rate, maintenance cost, repeat-failure rate
- Optimized Delta layout (partitioning / Z-order / liquid clustering) with a short performance rationale
- Silver → Gold reconciliation of row counts and critical totals
- Table and column descriptions (comments on Delta tables)

## Current state

`fact_sensor_reading.sql` is a placeholder (two comment lines). The Gold tables that the API, the agent and the Fabric model
already use (`<prefix>gold_*`, prefix `test_` today: `gold_fact_sensor_reading`, `gold_fact_maintenance_work_order`,
`gold_fact_downtime`, `gold_kpi_equipment_month` and the dimensions `dim_date`, `dim_equipment`, `dim_equipment_type`,
`dim_site`, `dim_failure_mode`, `dim_maintenance_type`) are built by a Gold notebook that is not in this folder: a copy is in
`.claude/reference/gold_layer.ipynb`, documented in `.claude/reference/gold_layer_data_dictionary.md`. Move it here when it is final.

