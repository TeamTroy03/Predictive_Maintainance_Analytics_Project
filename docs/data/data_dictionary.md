# Data dictionary

**Spec:** §4 "A data dictionary describing fields, types, keys, business meaning and sensitivity."

One table per source file (and later per Silver/Gold table). Profiled from `src/data/raw/` on 2026-10-02.

## Sources at a glance

| File | Source system | Grain | Rows | Distinct IDs |
|---|---|---|---|---|
| `equipment_2.csv` | `EAM` (enterprise asset management) | One equipment *version* (SCD history) | 10,000 | 9,650 `equipment_id` (+100 rows with no ID) |
| `maintenance_history_2.csv` | `CMMS` + `LEGACY_CMMS` | One maintenance activity version | 10,000 | 9,800 `maintenance_id` |
| `work_orders_2.csv` | `CMMS` | One work order version | 10,000 | 9,572 `work_order_id` |
| `sensor_events_2.json` | `IOT_HUB` | One sensor reading version (nested JSON array) | 10,000 | 9,870 `event_id` |

The files hold two extract batches: `BATCH-20260915-01` (initial load) and `BATCH-20260922-02` (incremental: new records, updates and corrections). `record_version` increments when a source record changes, so the business ID alone is **not** unique in the raw files. Bronze keeps all versions; Silver keeps the latest per ID.

**Conventions**
- **Types** are logical target types. Timestamps are ISO-8601 UTC (`...Z`). Local time needs `equipment.site_timezone`.
- **Key:** `PK` = business key of the entity, `FK →` = reference to another entity, `BK` = alternate business key.
- **Sensitivity:** all data is synthetic. The classes below describe how the equivalent real data would be handled:
  - `Internal`: operational data, fine inside the company.
  - `Confidential`: commercial data (costs, rates, supplier and serial details).
  - `Pseudonymous`: an ID that maps to a person in an HR system. Treat as personal data (GDPR), and don't join it to names in analytics.

## `equipment_2.csv`

Equipment master: one row per asset, with nameplate ratings and lifecycle dates. Reference data for every other source.

| Field | Type | Key | Business meaning | Sensitivity |
|---|---|---|---|---|
| `equipment_id` | string | PK | Asset identifier `EQ-nnnnn` (10001–19750) | Internal |
| `equipment_name` | string | | Display name, always `<equipment_type> <asset_tag>` | Internal |
| `asset_tag` | string | BK | Plant tag `<type prefix>-<site letter><seq>`, e.g. `CNC-B0001` (prefixes in [code lists](#equipment-types)) | Internal |
| `equipment_type` | string | | Asset class: one of 9 types | Internal |
| `manufacturer` | string | | OEM (7 values, e.g. `Nordwerk Maschinenbau`) | Confidential |
| `model_number` | string | | OEM model, e.g. `NWM-AC160F` (1,953 values) | Confidential |
| `serial_number` | string | BK | OEM serial number, unique per asset | Confidential |
| `site_code` | string | | Plant code: `PLT-A`, `PLT-B` | Internal |
| `site_name` | string | | `Rheinfeld Works` (PLT-A), `Valmora Plant` (PLT-B) | Internal |
| `country` | string | | `Germany` (PLT-A), `Spain` (PLT-B) | Internal |
| `site_timezone` | string | | IANA zone: `Europe/Berlin`, `Europe/Madrid` | Internal |
| `production_line` | string | | Line within the site: `L1`–`L4` | Internal |
| `parent_equipment_id` | string | FK → `equipment_id` | Parent asset in the hierarchy (e.g. motor driving a pump). Null for top-level assets (87%) | Internal |
| `install_date` | date | | Commissioning date | Internal |
| `decommission_date` | date | | Retirement date. Set only when `status = Decommissioned` | Internal |
| `criticality` | string | | Business criticality `A` (highest) / `B` / `C` | Internal |
| `rated_power_kw` | decimal | | Nameplate power, kW | Internal |
| `rated_speed_rpm` | integer | | Nameplate speed, rpm | Internal |
| `rated_temp_max_c` | integer | | Maximum allowed operating temperature, °C. Equals the `alarm.limit_value` of `TMP_HIGH` alarms on this asset | Internal |
| `rated_vibration_max_mm_s` | decimal | | Vibration alarm limit, mm/s RMS (ISO 10816 zone values 2.8 / 4.5 / 6.3 / 7.1). Equals the `alarm.limit_value` of `VIB_HIGH` alarms | Internal |
| `rated_pressure_max_bar` | decimal | | Maximum pressure, bar. Only for pressure equipment (pumps, compressors, presses); null for the rest | Internal |
| `operating_hours_per_day` | integer | | Planned operating hours per day (16 or 24) | Internal |
| `operating_days_per_week` | integer | | Planned operating days per week (5, 6, 7) | Internal |
| `status` | string | | `Active` / `Decommissioned` | Internal |
| `effective_from` | date | | Start of validity of this version. Equals `install_date` for version 1; the change date for later versions (SCD Type 2 input) | Internal |
| `last_modified_ts` | timestamp | | Last change in EAM | Internal |
| `record_version` | integer | | Version of this equipment record (1, 2) | Internal |
| `source_file`, `ingestion_ts`, `batch_id`, `source_system`, `source_system_key` | | | Extract lineage, see [lineage columns](#lineage-columns) | Internal |

## `maintenance_history_2.csv`

Maintenance activities actually performed: what was done, on which component, and at what cost.

| Field | Type | Key | Business meaning | Sensitivity |
|---|---|---|---|---|
| `maintenance_id` | string | PK | Activity identifier `MH-nnnnn` | Internal |
| `work_order_id` | string | FK → `work_orders.work_order_id` | Work order the activity was executed under. Null only for the 50 `LEGACY_CMMS` records (Oct 2025 – Mar 2026, before the current CMMS). A work order has 1 or 2 activities | Internal |
| `equipment_id` | string | FK → `equipment.equipment_id` | Asset worked on. Always matches the work order's asset, so 32 of the 42 missing values can be recovered through `work_order_id` (the other 10 belong to work orders that are also missing it) | Internal |
| `activity_start_ts` | timestamp | | Work started | Internal |
| `activity_end_ts` | timestamp | | Work finished | Internal |
| `activity_type` | string | | `Inspect`, `Replace`, `Align`, `Clean`, `Service`, `Calibrate`, `Lubricate`, `Repair`, `Adjust` | Internal |
| `component` | string | | Component worked on (human-readable) | Internal |
| `component_code` | string | | Component code, see [code lists](#components) | Internal |
| `failure_mode_code` | string | FK → failure mode | Failure addressed. `NONE` = planned work with no failure | Internal |
| `root_cause_code` | string | FK → root cause | Root cause of the failure. Null exactly when `failure_mode_code = NONE` | Internal |
| `detection_method` | string | | How the need was found: `scheduled_pm`, `inspection`, `operator_report`, `condition_monitoring`, `alarm` | Internal |
| `action_description` | string | | Free-text action summary (65 templated values) | Internal |
| `parts_qty` | integer | | Number of spare parts used | Internal |
| `parts_cost_eur` | decimal(12,2) | | Spare-parts cost of this activity, **always EUR**. A breakdown of the work order's `parts_cost`, so never add the two together | Confidential |
| `labor_hours` | decimal(8,2) | | Technician hours of this activity. The activities of a closed work order sum to its `labor_hours` (8,744 of 8,949; the rest are defect rows) | Internal |
| `technician_id` | string | | Technician `TECH-01`–`TECH-08` | Pseudonymous |
| `outcome` | string | | `Resolved` or `Temporary fix` (follow-up needed; drives repeat-failure analysis) | Internal |
| `next_due_date` | date | | Next planned occurrence. Set only for planned work (`failure_mode_code = NONE`) | Internal |
| `last_modified_ts` | timestamp | | Last change in CMMS (normally `activity_end_ts` + 30 min) | Internal |
| `record_version` | integer | | Version of this record (1, 2) | Internal |
| `source_file`, `ingestion_ts`, `batch_id`, `source_system`, `source_system_key` | | | Extract lineage, see [lineage columns](#lineage-columns) | Internal |

## `work_orders_2.csv`

Maintenance work orders: request, priority, lifecycle timestamps, downtime caused and cost.

| Field | Type | Key | Business meaning | Sensitivity |
|---|---|---|---|---|
| `work_order_id` | string | PK | Work order identifier `WO-nnnnn` | Internal |
| `equipment_id` | string | FK → `equipment.equipment_id` | Asset the work order is for | Internal |
| `wo_type` | string | | `Preventive`, `Corrective`, `Inspection`, `Predictive` | Internal |
| `priority` | string | | `P1`–`P4`; sets the due date (see [code lists](#work-order-priority)) | Internal |
| `failure_mode_code` | string | FK → failure mode | Failure the work order addresses. `NONE` for planned work | Internal |
| `failure_mode_desc` | string | | Description of `failure_mode_code` (1:1) | Internal |
| `description` | string | | Free text, e.g. `60-day preventive maintenance on P-A0159`, `Lubrication failure: rising oil and bearing temperature on AC-A0457` | Internal |
| `requested_by_role` | string | | Requester role: `Planner`, `Operator`, `Shift Supervisor`, `Condition Monitoring` (a role, not a person) | Internal |
| `assigned_technician_id` | string | | Technician `TECH-01`–`TECH-08` | Pseudonymous |
| `created_ts` | timestamp | | Work order raised | Internal |
| `due_ts` | timestamp | | Target completion = `created_ts` + priority SLA. Basis for *overdue work orders* | Internal |
| `actual_start_ts` | timestamp | | Work started: the first maintenance activity's `activity_start_ts`, or `downtime_start_ts` when there is no activity. Null for `Open` and `Cancelled` | Internal |
| `downtime_start_ts` | timestamp | | Equipment stopped. Can precede `created_ts` for breakdowns (the WO is raised after the stop) | Internal |
| `downtime_end_ts` | timestamp | | Equipment back in service. Null while downtime is ongoing | Internal |
| `downtime_minutes` | integer | | `downtime_end_ts − downtime_start_ts` in minutes (always consistent) | Internal |
| `downtime_type` | string | | `unplanned` (Corrective WOs only) / `planned`. Null when there was no downtime | Internal |
| `closed_ts` | timestamp | | Closed or cancelled. Null for `Open` and `In Progress` | Internal |
| `status` | string | | `Open`, `In Progress`, `Closed`, `Cancelled` | Internal |
| `cancellation_reason` | string | | Set only for `Cancelled`: `Scope merged into next PM`, `Asset not available - production priority`, `Raised in error` | Internal |
| `root_cause_code` | string | FK → root cause | Root cause. Filled only on `Closed` Corrective/Predictive work orders; blank for planned, cancelled and not-yet-closed work | Internal |
| `triggering_event_id` | string | FK → `sensor_events.event_id` | Sensor event that triggered a `Predictive` WO (19 rows) | Internal |
| `labor_hours` | decimal(8,2) | | Technician hours booked. Null until the WO is closed | Internal |
| `labor_cost` | decimal(12,2) | | Labour cost: `labor_hours` × 55.00 EUR/h, converted to `currency` | Confidential |
| `parts_cost` | decimal(12,2) | | Spare-parts cost in `currency` | Confidential |
| `currency` | string | | ISO 4217 currency of the cost fields: `EUR` (92%), `GBP`, `USD`. Must be converted to EUR in Silver. The repo has no FX table yet; the rates implied by `labor_cost` vary by row (≈0.853–0.858 GBP and ≈1.086–1.100 USD per EUR), so a dated rate source is needed | Internal |
| `last_modified_ts` | timestamp | | Last change in CMMS | Internal |
| `record_version` | integer | | Version of this record. Version 2 mostly records the status progressing to `Closed` | Internal |
| `source_file`, `ingestion_ts`, `batch_id`, `source_system`, `source_system_key` | | | Extract lineage, see [lineage columns](#lineage-columns) | Internal |

## `sensor_events_2.json`

IoT readings: a JSON array with one nested object per reading. The readings cover 84 sensors on 20 monitored assets (plus the `EQ-9999` placeholder); there are no readings for the rest of the fleet.

| Field (JSON path) | Type | Key | Business meaning | Sensitivity |
|---|---|---|---|---|
| `event_id` | string | PK | Reading identifier `SE-nnnnnn` | Internal |
| `record_version` | integer | | 1 = original, 2 = correction re-sent by the IoT hub | Internal |
| `event_ts` | timestamp | | Measurement time at the sensor | Internal |
| `equipment_id` | string | FK → `equipment.equipment_id` | Monitored asset | Internal |
| `operating_state` | string | | Asset state at measurement: `running`, `stopped`, `maintenance` | Internal |
| `sensor.sensor_id` | string | | Sensor `<equipment_id>-<TYPE><nn>`, e.g. `EQ-15800-CUR01` (TMP, VIB, CUR, PRS, SPD, LOD) | Internal |
| `sensor.sensor_type` | string | | `temperature`, `vibration`, `current`, `pressure`, `speed`, `load` | Internal |
| `sensor.measure` | string | | Measured quantity, see [code lists](#sensor-measures) | Internal |
| `sensor.canonical_unit` | string | | Unit the sensor is configured for: `degC`, `mm/s`, `A`, `bar`, `rpm`, `%` | Internal |
| `sensor.range_min` | decimal | | Lower bound of the valid physical range (0, or −40 for temperature) | Internal |
| `sensor.range_max` | decimal | | Upper bound of the valid physical range (depends on the asset) | Internal |
| `sensor.measurement_point` | string | | Mounting point: `DE-BEARING`, `MOTOR-TERMINAL`, `DISCHARGE`, `OIL-SUMP`, `SHAFT`, `WINDING`, `OUTPUT-SHAFT` | Internal |
| `reading.value` | decimal | | Measured value | Internal |
| `reading.unit` | string | | Unit of `value` as sent. Normally equals `canonical_unit`; can also be `degF`, `in/s`, `psi` | Internal |
| `quality_flag` | string | | Gateway quality verdict: `GOOD`, `SUSPECT` | Internal |
| `alarm.state` | string | | `normal`, `warning`, `alarm`. Set by the gateway on the raw value, so it is wrong for unit errors and spikes (67 of the 100 unit-mismatch rows show `alarm`). Silver recomputes it | Internal |
| `alarm.code` | string | | `VIB_HIGH`, `TMP_HIGH`, `PRS_HIGH`, `CUR_HIGH`. Null when `normal` | Internal |
| `alarm.limit_value` | decimal | | Threshold that was crossed. For `alarm` it equals the asset's rated maximum; `warning` uses a lower threshold. Null when `normal` | Internal |
| `gateway.gateway_id` | string | | Edge gateway `GW-<site>-<line>`, e.g. `GW-PLTB-L1` (always matches `location`) | Internal |
| `gateway.firmware_version` | string | | Gateway firmware: `3.9.7`, `4.2.1` | Internal |
| `gateway.received_ts` | timestamp | | Time the gateway received the reading. Lateness = `received_ts − event_ts` | Internal |
| `location.site_code` | string | | Site of the asset (always matches `equipment.site_code`) | Internal |
| `location.production_line` | string | | Line of the asset (always matches `equipment.production_line`) | Internal |
| `tags` | array\<string\> | | Mounting-point tag (`drive-end`, `motor-terminal`, …) plus flags: `buffered-upload` (late-arriving), `reprocessed` (correction), `unregistered` (unknown asset). Informational only: derive these conditions from the timestamps, `record_version` and `equipment_id`, not from tags | Internal |
| `correction.is_correction` | boolean | | Present only in the 2026-09-22 batch (schema drift Bronze must tolerate). `true` = this version replaces an earlier one | Internal |
| `correction.corrects_version` | integer | | Version being replaced (always 1) | Internal |
| `correction.reason` | string | | Why it was corrected (`Out-of-range value re-processed by IoT hub`) | Internal |
| `_metadata.source_file`, `.ingestion_ts`, `.batch_id`, `.source_system`, `.source_system_key` | | | Extract lineage, see [lineage columns](#lineage-columns) | Internal |

## Code lists

### Equipment types

Typical nameplate values. The rare outliers are defects (see [below](#known-raw-defects)).

| `equipment_type` | Tag prefix | Rows | Typical rpm | Max temp °C | Has pressure rating |
|---|---|---|---|---|---|
| Induction Motor | `M` | 1,847 | 990 / 1480 / 2960 | 155 | no |
| Centrifugal Pump | `P` | 1,796 | 1480 / 2960 | 95 | yes (16 bar) |
| Conveyor Drive Motor | `CDM` | 1,383 | 1460 | 155 | no |
| Air Compressor | `AC` | 1,175 | 1480 / 2960 | 110 | yes (13 bar) |
| CNC Spindle | `CNC` | 994 | 12000 / 18000 / 24000 | 80 | no |
| Gearbox | `GB` | 898 | 1480 | 95 | no |
| Cooling Tower Fan | `CTF` | 716 | 1480 | 90 | no |
| Hydraulic Press | `HP` | 692 | 1480 | 70 | yes (315 bar) |
| Boiler Feed Pump | `BFP` | 499 | 2980 | 95 | yes (200 bar) |

### Failure modes

Shared by `work_orders.failure_mode_code` and `maintenance_history.failure_mode_code`. Descriptions come from `work_orders.failure_mode_desc`.

| Code | Description |
|---|---|
| `NONE` | No failure (planned work) |
| `BRG-WEAR` | Bearing wear |
| `MISALIGN` | Shaft misalignment |
| `IMBAL` | Rotor imbalance |
| `OVERHEAT` | Overheating |
| `LUB-FAIL` | Lubrication failure |
| `WIND-INS` | Stator winding insulation breakdown |
| `SEAL-LEAK` | Mechanical seal leakage |
| `CAVIT` | Cavitation |
| `VALVE-FAIL` | Valve failure |
| `GEAR-WEAR` | Gear tooth wear |
| `CLOG` | Blocked suction / plugged strainer |

### Root causes

No description is supplied in the sources. The meanings below are read from the code names; confirm them with the business before publishing.

| Code | Meaning (inferred) |
|---|---|
| `RC-WEAR` | Normal wear and tear |
| `RC-CONTAM` | Contamination (dirt, water, debris) |
| `RC-LUB` | Lubrication issue (wrong, missing or degraded lubricant) |
| `RC-INST` | Installation error |
| `RC-OPER` | Operating error / operating outside limits |
| `RC-MAINT` | Maintenance-induced (poor previous workmanship) |
| `RC-DESIGN` | Design deficiency |
| `RC-MATL` | Material defect |

### Components

| `component_code` | `component` |
|---|---|
| `GENERAL` | Whole asset |
| `COUPLING` | Shaft coupling |
| `BRG-DE` | Drive-end bearing. The composite `Bearing and coupling` (89 records) maps to its primary part, `BRG-DE` |
| `BRG-NDE` | Non-drive-end bearing |
| `COOLING-SYS` | Cooling system / heat exchanger |
| `LUBE-SYS` | Lubrication system |
| `SENSOR-PKG` | Sensor package |
| `OIL-FILTER` | Oil filter |
| `ROTOR` | Rotor assembly |
| `STATOR-WDG` | Stator winding |
| `SEAL-MECH` | Mechanical seal |
| `IMPELLER` | Impeller |
| `VALVE` | Valve assembly |
| `GEAR-SET` | Gear set |
| `STRAINER` | Suction strainer / filter |
| `FAN-BLADE` | Fan blade set |

### Work order priority

| Priority | Due after creation | Mostly used for |
|---|---|---|
| `P1` | 24 h | Corrective (breakdowns) |
| `P2` | 72 h | Corrective, Predictive |
| `P3` | 7 days | Preventive, Inspection, Predictive |
| `P4` | 14 days | Preventive, Inspection |

### Work order type

| `wo_type` | Requested by | Downtime |
|---|---|---|
| `Preventive` | Planner | planned or none |
| `Inspection` | Planner | planned or none |
| `Corrective` | Operator, Shift Supervisor (rarely Condition Monitoring) | unplanned (counts toward *unplanned downtime*) |
| `Predictive` | Condition Monitoring (19 linked to a `triggering_event_id`) | planned or none |

### Sensor measures

| `sensor_type` | `measure` | Canonical unit | Measurement point | Alarm code |
|---|---|---|---|---|
| temperature | `bearing_temperature`, `oil_temperature`, `winding_temperature` | `degC` | `DE-BEARING`, `OIL-SUMP`, `WINDING` | `TMP_HIGH` |
| vibration | `vibration_rms` | `mm/s` | `DE-BEARING` | `VIB_HIGH` |
| current | `motor_current` | `A` | `MOTOR-TERMINAL` | `CUR_HIGH` |
| pressure | `discharge_pressure` | `bar` | `DISCHARGE` | `PRS_HIGH` |
| speed | `shaft_speed` | `rpm` | `SHAFT` | — |
| load | `load_pct` | `%` | `OUTPUT-SHAFT` | — |

### Analytical labels

The sources have no ready-made target column for failure prediction. These fields define the outcomes:

| Label | Derivation |
|---|---|
| Failure event | Work order with `wo_type = Corrective` (or `failure_mode_code ≠ NONE`), dated by `downtime_start_ts` or `created_ts` |
| Failure class | `failure_mode_code` (11 failure classes + `NONE`) |
| Unplanned downtime | `downtime_minutes` where `downtime_type = unplanned` |
| Abnormal reading | `alarm.state` in (`warning`, `alarm`) |
| Repeat failure | Same `equipment_id` + `failure_mode_code` recurring after an `outcome = Temporary fix`, or within a set window |

## Lineage columns

**Delivered by the source extracts.** In the CSVs they are top-level columns; in the JSON they sit under `_metadata`.

| Field | Type | Meaning |
|---|---|---|
| `source_file` | string | Original extract file name, e.g. `cmms_work_orders_extract_20260915.csv` |
| `ingestion_ts` | timestamp | Extract time of the batch (`2026-09-15T02:00:00Z` or `2026-09-22T02:00:00Z`) |
| `batch_id` | string | Extract batch: `BATCH-20260915-01`, `BATCH-20260922-02` |
| `source_system` | string | `EAM`, `CMMS`, `LEGACY_CMMS`, `IOT_HUB` |
| `source_system_key` | string | `<source_system>\|<business id>`, unique per entity across systems |

**Added by Bronze** (all Bronze tables, from `databricks/common/`):

| Field | Type | Meaning |
|---|---|---|
| `_ingestion_ts` | timestamp | When the record was ingested |
| `_batch_id` | string | Ingestion batch |
| `_source_file` | string | Source filename |
| `_source_system` | string | Source-system key |
| `_record_hash` | string | Hash of source record for dedup/idempotency |
| `_load_status` | string | Load outcome |

## Known raw defects

These defects were injected deliberately, and Silver must detect or fix them. Counts are raw rows. Detection evidence goes in [`quality_evidence.md`](../quality_evidence.md).

| Defect | Equipment | Maintenance | Work orders | Sensor events |
|---|---|---|---|---|
| Exact duplicate rows | 100 | 100 | 100 | 100 |
| Newer version of same ID (keep latest, not a defect) | 150 (SCD changes: criticality, line, site) | 100 | 328 | 30 corrections |
| Missing `equipment_id` | 100 (asset still identifiable by unique `asset_tag` / `serial_number`, but no ID to recover) | 42 (32 recoverable via `work_order_id`) | 50 | 50 (recoverable via `sensor_id` prefix) |
| Unknown `equipment_id` `EQ-9999` | — | 58 | 50 | 50 (tagged `unregistered`) |
| Timeline inconsistency | 80 `install_date` after the extract date; 100 `decommission_date` < `install_date` | 100 `activity_end_ts` < `activity_start_ts` | 100 `closed_ts` < `created_ts` | — |
| Overlapping downtime (same asset) | — | — | 101 interval pairs | — |
| Unit inconsistency | 34 temp in °F, 49 vibration in in/s, 17 pressure in psi, 24 power in W | — | 764 costs in GBP/USD | 41 °F, 33 in/s, 26 psi |
| Negative values | 18 power, 19 vibration | 25 `parts_qty`, 21 `parts_cost_eur`, 32 `labor_hours` | 24 each of `labor_hours`, `labor_cost`, `parts_cost` | 26 readings < 0 (subset of out-of-range) |
| Sentinel / impossible values | 11 rpm = 99999, 10 rpm = 0, 18 hours/day = 30 | 22 `labor_hours` = 999 | 28 `labor_hours` = 9999 | 45 readings = 9999 (13 later corrected) |
| Out-of-range reading (same unit) | — | — | — | 100 (75 flagged `SUSPECT`) |
| Late-arriving | — | — | — | 150 `buffered-upload` (2–16 days late); corrections arrive up to 187 days late |
