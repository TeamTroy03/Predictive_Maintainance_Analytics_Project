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

## Baseline and increments

The committed `*_2.*` files are the baseline plant (9,750 assets, 2026-03-15 .. 2026-09-22; `equipment_2.csv`,
`work_orders_2.csv`, `maintenance_history_2.csv`, `sensor_events_2.json`). `chain.json` lists them and every later
increment (`work_orders_<ts>.csv`, `maintenance_history_<ts>.csv`, `sensor_events_<ts>.json`, no equipment file).
Add increments only with `generate_data.py --continue`; a run without it creates a different plant that conflicts with
these files. Commit `chain.json` together with the increment files.
