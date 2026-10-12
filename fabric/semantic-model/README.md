# Semantic model

**Spec:** §6.4

**Acceptance condition:** relationships, hierarchies, formats, descriptions and a dedicated
measures table are defined; filter direction and measure behavior are validated against the
date table; RLS by site is implemented and tested.

## Required measures (Team 3 KPIs, ≥ 8)

Asset availability · Unplanned downtime · MTBF · MTTR · Overdue work orders · Alarm rate ·
Maintenance cost · Repeat-failure rate

## Current content

Power BI model-view screenshots of the relationships over the Gold tables: three facts (`gold_fact_maintenance_work_order`,
`gold_fact_sensor_reading`, `gold_fact_downtime`), the monthly `gold_kpi_equipment_month` table and the dimensions
`dim_date`, `dim_equipment`, `dim_equipment_type`, `dim_site`, `dim_failure_mode`, `dim_maintenance_type`
(`Semantic_model_full.png` is the complete model, the other images show parts of it, including the multi-fact layout).
The TMDL source, the measures table and the RLS roles are not committed yet.

