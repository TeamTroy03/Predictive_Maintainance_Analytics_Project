# Data

**Spec:** §4 Dataset · **Deliverable:** Business and data brief → `docs/business_brief.md`
**Owner:** @participant1

**Acceptance condition (§4 minimums):**
- Transactional CSV: `maintenance_history.csv`, `work_orders.csv` (related, with dates, IDs, costs/durations)
- Reference CSV: `equipment.csv`
- Semi-structured JSON: `sensor_events.json`
- ≥ 10,000 transactional/event records in total
- Deliberately injected quality defects (see below)
- Synthetic only: no real personal or confidential information

## Layout

| Folder | Contents |
|---|---|
| `raw/` | The four source files as delivered to Bronze |
| `generate/` | Script that produces `raw/` reproducibly (fixed seed) |

## Defects to inject (Team 3 priority DQ rules)

- Out-of-range sensor readings
- Unit inconsistency (e.g. °C vs °F, bar vs psi)
- Missing / unknown equipment keys
- Overlapping downtime intervals
- Work order closed before it was created
- Duplicate sensor events
