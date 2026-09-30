# 01 — Business and data brief

**Spec:** §7 Deliverable "Business and data brief"; §2, §3, §4 · **Owners:** @participant1, @participant5

**Required evidence:** problem statement, users, decisions, KPIs, dataset inventory and data dictionary.

## Problem statement
_TODO: equipment health, abnormal sensor behavior, maintenance effectiveness, downtime, recurring failure modes._

## Users and decisions
| User | Decision supported |
|---|---|
| _TODO_ | |

## KPIs
| KPI | Definition | Source measure |
|---|---|---|
| Asset availability | | |
| Unplanned downtime | | |
| Mean time between failures (MTBF) | | |
| Mean time to repair (MTTR) | | |
| Overdue work orders | | |
| Alarm rate | | |
| Maintenance cost | | |
| Repeat-failure rate | | |

## Dataset inventory
| File | Class | Records | Grain | Defects injected |
|---|---|---|---|---|
| `equipment.csv` | Reference | | | |
| `maintenance_history.csv` | Transactional | | | |
| `work_orders.csv` | Transactional | | | |
| `sensor_events.json` | Semi-structured | | | |

See also: [`data-dictionary.md`](data-dictionary.md), [`refresh-strategy.md`](refresh-strategy.md).
