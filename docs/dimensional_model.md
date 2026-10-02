# 05 — Dimensional model

**Spec:** §7 Deliverable "Dimensional model"; §6.3 · **Owner:** @participant2

**Required evidence:** grain statements, star-schema diagram, keys, dimensions, facts and mapping.

## Star-schema diagram
_TODO_

## Facts
| Fact | Grain | Measures | Dimension keys |
|---|---|---|---|
| `fact_sensor_reading` | One sensor reading per equipment per timestamp | | |
| `fact_maintenance_work_order` | One maintenance work order | | |
| `fact_downtime` | _TODO: one downtime interval per equipment_ | | |

## Dimensions
| Dimension | Surrogate key | Natural key | SCD type | Unknown member |
|---|---|---|---|---|
| `dim_date` | | | — | |
| `dim_equipment` | | | | |
| `dim_site` | | | | |
| `dim_equipment_type` | | | | |
| `dim_failure_mode` | | | | |
| `dim_maintenance_type` | | | | |

## Business measures
_TODO: definitions of availability, MTBF, MTTR, etc. as computed in Gold._

## Delta layout and performance rationale
_TODO_

## Mapping
See [`source_to_target.md`](source_to_target.md).
