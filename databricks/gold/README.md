# Gold dimensional model

**Spec:** §6.3 Databricks: Gold Dimensional Model (Delta) · **Owner:** @participant2
**Docs:** `docs/05-dimensional-model/`

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
