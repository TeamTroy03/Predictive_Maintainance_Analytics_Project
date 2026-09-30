# Silver preparation

**Spec:** §6.2 Databricks: Silver Preparation · **Owner:** @participant1

**Acceptance condition:** conformed records are analytically consistent, quality results are
measurable, and rejected records remain visible and actionable.

## Must implement

- Standardize names, types, dates, time zones, codes, currencies and **units** (sensor unit conversion)
- Domain rules, deduplication (duplicate sensor events), resolve invalid/missing equipment keys
- Flatten `sensor_events.json`, join equipment reference data
- DQ expectations: completeness, validity, uniqueness, referential integrity
- Quarantine table with reason, rule and processing timestamp
- Documented treatment of slowly changing equipment attributes and late-arriving sensor events

## Team 3 priority rules

Out-of-range readings · unit inconsistency · missing equipment keys · overlapping downtime ·
closure before creation · duplicate sensor events
