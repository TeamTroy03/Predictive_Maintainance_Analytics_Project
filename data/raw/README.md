# Raw source files

**Spec:** §4 Dataset, input to §6.1 Bronze · **Owner:** @participant1

**Acceptance condition:** the four Team 3 source files exist here with the defects in `../README.md`
and are ingestible by `databricks/bronze/` without manual edits.

| File | Class | Content |
|---|---|---|
| `equipment.csv` | Reference | Equipment master: ID, type, site, install date, rated limits |
| `maintenance_history.csv` | Transactional | Completed maintenance/failure events, failure mode, cost |
| `work_orders.csv` | Transactional | Work orders: created/due/closed timestamps, type, status |
| `sensor_events.json` | Semi-structured | Nested sensor readings per equipment and timestamp |

Keep files small enough to refresh live during the demo.
