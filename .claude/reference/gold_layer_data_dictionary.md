# Gold Layer Data Dictionary: Team 3 Predictive Maintenance

This document describes every table the Gold part of the pipeline creates: 6 dimensions, 3 fact tables, and 1 KPI table plus its view, and the 2 knowledge graph tables (section 8). Sections 1-7 are derived from the Gold cells of `test_pipeline.ipynb` (Gold cells 1, 2 and 3 of 3); the knowledge graph tables of section 8 were built in Databricks from those Gold tables.

All tables are Delta tables in `{CATALOG_NAME}.{SCHEMA_NAME}` (currently `team3`). Names carry a `test_` prefix in the notebook, for example `test_gold_dim_site`. Table names below are shown without the prefix.

---

## 1. Overview

```
Silver tables                       Gold dimensions                 Gold facts                          Gold KPI layer
------------------------------      ---------------------------     --------------------------------    ---------------------------------
silver_equipment              -->   dim_site                        fact_sensor_reading                 gold_kpi_equipment_month  (table)
silver_work_orders            -->   dim_equipment_type              fact_maintenance_work_order         gold_vw_kpi_equipment_month_v2 (view)
silver_maintenance_history    -->   dim_equipment                   fact_downtime
silver_sensor_events          -->   dim_date
(none, hard-coded in code)    -->   dim_failure_mode
(none, hard-coded in code)    -->   dim_maintenance_type
```

Run order is fixed: **dimensions, then facts, then KPI table**. Facts look up dimension keys, and the KPI table reads the facts and `dim_date`.

### Settings that shape the data

| Setting | Value | Effect |
|---|---|---|
| `UNKNOWN_KEY` | `-1` | Key of the "Unknown" member added to every dimension |
| `AS_OF` | Latest timestamp found in Silver sensor events and work orders (override with `AS_OF_OVERRIDE`) | Fixed once per run. Used for overdue work orders, ongoing downtime and the KPI calendar |
| `CLOSED_STATUSES` | `Closed`, `Cancelled` | A work order is **open** when its status is not in this list |
| `CANCELLED_STATUSES` | `Cancelled` | Cancelled work orders stay as rows but are excluded from `hours_to_close` |
| `UNPLANNED_TYPES` | `Corrective` | Only corrective work orders are unplanned |
| `ALARM_STATES` | `warning`, `alarm` | A reading is an alarm (`is_alarm`) when its `alarm_state` is in this list |
| `TEMP_FIX` | `Temporary fix` | Value of `outcome` in maintenance history that marks a temporary fix |
| `REPEAT_DAYS` | `30` | Repeat-failure window |
| Time zone | UTC (`spark.sql.session.timeZone`) | All timestamps are UTC |

### Conventions used in every table

- **Surrogate keys** are `abs(xxhash64(business_key))`. The same business key always produces the same surrogate key, on every run. There are no sequences.
- **Unknown member**: every dimension has one row with key `-1`. Fact rows whose business key is missing or not found in the dimension get `-1`. Other columns of the Unknown row are `NULL`, except the descriptive columns that are set explicitly (see each dimension).
- **Date keys** are integers in `yyyyMMdd` format (for example `20260315`). `NULL` dates become `-1`.
- **Equipment IDs** are normalized with `upper(trim())` before any join, because casing differs between Silver tables.
- **`gold_loaded_ts`** is added to every table at save time (`current_timestamp()`). It is the "last refresh" indicator for Power BI.
- **Full overwrite**: every Gold table is rebuilt on each run (`mode = overwrite`, `overwriteSchema = true`). Gold is not incremental. Schema changes do not fail loudly, so the reconciliation checks are the safety net.

---

## 2. Dimension tables

### 2.1 `gold_dim_date`

**What it contains:** one row per calendar day, for whole months from the first to the last date found in the data. `due_ts` is included, so the range extends into the future. Plus the Unknown row.

**Grain:** one calendar day. **Key:** `date_key`.

| Column | Description |
|---|---|
| `date_key` | Day as integer `yyyyMMdd`. `-1` = Unknown |
| `full_date` | The calendar date |
| `year` | Calendar year |
| `quarter` | Quarter number (1-4) |
| `month_number` | Month number (1-12) |
| `month_name` | Full month name, for example `March` |
| `year_month` | `yyyy-MM`. `UNKNOWN` for the Unknown row |
| `month_start_date` | First day of the month. The KPI table joins on this |
| `day_name` | Full weekday name |
| `is_weekend` | `true` for Saturday and Sunday |
| `gold_loaded_ts` | Load timestamp |

### 2.2 `gold_dim_site`

**What it contains:** one row per distinct `site_code` found in the current equipment records.

**Grain:** one site. **Key:** `site_key` (hash of `site_code`). **Source:** `silver_equipment`.

| Column | Description |
|---|---|
| `site_key` | Surrogate key. `-1` = Unknown |
| `site_code` | Cleaned (`upper(trim())`) site code. `UNKNOWN` for the Unknown row |
| `site_name` | Site name (`max()` over the equipment rows of the site) |
| `country` | Country |
| `site_timezone` | Local time zone of the site |
| `gold_loaded_ts` | Load timestamp |

### 2.3 `gold_dim_equipment_type`

**What it contains:** one row per distinct equipment type (for example pump, motor, compressor, gearbox, conveyor).

**Grain:** one equipment type. **Key:** `equipment_type_key` (hash of `equipment_type`). **Source:** `silver_equipment`.

| Column | Description |
|---|---|
| `equipment_type_key` | Surrogate key. `-1` = Unknown |
| `equipment_type` | Type name. `UNKNOWN` for the Unknown row |
| `gold_loaded_ts` | Load timestamp |

### 2.4 `gold_dim_equipment`

**What it contains:** one row per piece of equipment, holding its **latest** attributes.

**Grain:** one equipment. **Key:** `equipment_key` (hash of `equipment_id`). **Source:** `silver_equipment`.

**History handling:** SCD type 1. Per `equipment_id` the row with the highest `record_version` (then latest `last_modified_ts`) is kept. Earlier attribute values are not stored in Gold.

| Column | Description |
|---|---|
| `equipment_key` | Surrogate key. `-1` = Unknown |
| `equipment_id` | Cleaned business ID. `UNKNOWN` for the Unknown row |
| `equipment_name` | Descriptive name |
| `manufacturer` | Manufacturer |
| `model_number` | Model number |
| `production_line` | Production line the asset belongs to |
| `criticality` | Criticality class of the asset |
| `status` | Equipment status (active, standby, decommissioned) |
| `install_date` | Installation date. Start of the asset's calendar hours |
| `decommission_date` | Decommission date. End of the asset's calendar hours |
| `rated_power_kw` | Rated power in kW |
| `operating_hours_per_day` | Scheduled operating hours per day. Used to build `operating_share` in the KPI table |
| `operating_days_per_week` | Scheduled operating days per week. Used for the same purpose |
| `site_key` | FK to `dim_site` (`-1` if the site code is missing) |
| `equipment_type_key` | FK to `dim_equipment_type` (`-1` if missing) |
| `gold_loaded_ts` | Load timestamp |

### 2.5 `gold_dim_failure_mode`

**What it contains:** a fixed list of failure modes defined in the notebook (not read from Silver).

**Grain:** one failure mode code. **Key:** `failure_mode_key` (hash of `failure_mode_code`).

| Column | Description |
|---|---|
| `failure_mode_key` | Surrogate key. `-1` = Unknown |
| `failure_mode_code` | Code. `UNKNOWN` for the Unknown row |
| `failure_mode_desc` | Description. `Unknown failure mode` for the Unknown row |
| `gold_loaded_ts` | Load timestamp |

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

### 2.6 `gold_dim_maintenance_type`

**What it contains:** a fixed list of four maintenance types defined in the notebook.

**Grain:** one maintenance type. **Key:** `maintenance_type_key` (hash of `maintenance_type`).

| Column | Description |
|---|---|
| `maintenance_type_key` | Surrogate key. `-1` = Unknown |
| `maintenance_type` | `Preventive`, `Corrective`, `Inspection`, `Predictive`. `UNKNOWN` for the Unknown row |
| `is_planned` | `false` only for `Corrective`. `Predictive` counts as planned (condition-triggered but scheduled) |
| `gold_loaded_ts` | Load timestamp |

---

## 3. Fact tables

All facts carry the keys of the equipment dimensions (`equipment_key`, `site_key`, `equipment_type_key`). Any key without a match is set to `-1`. The reconciliation checks verify that no fact key is orphaned.

### 3.1 `gold_fact_sensor_reading`

**What it contains:** one row per sensor measurement, in SI units, with its alarm status.

**Grain:** one reading per **equipment, sensor and timestamp**. **Source:** `silver_sensor_events`.

**Deduplication:** Silver removes only exact duplicates. Gold keeps one row per `(equipment_id, sensor_id, event_timestamp)`: the one with the highest `record_version` (ties broken by `event_id`).

**Layout:** `OPTIMIZE ... ZORDER BY (equipment_key, date_key)`. This is the only large table, and it is filtered by asset and date.

| Column | Description |
|---|---|
| `date_key` | FK to `dim_date`, day of the reading |
| `equipment_key` | FK to `dim_equipment` |
| `site_key` | FK to `dim_site` |
| `equipment_type_key` | FK to `dim_equipment_type` |
| `event_ts` | Timestamp of the reading (UTC). Silver name: `event_timestamp` |
| `equipment_id` | Equipment business ID |
| `sensor_id` | Sensor ID, for example `PMP-003-VIB-01` |
| `sensor_type` | Sensor type (vibration, bearing temperature, current, pressure, and so on) |
| `event_id` | ID of the source event that carried the reading |
| `operating_state` | Operating state of the asset at the time (running, stopped, maintenance) |
| `reading_value` | Measured value **in the canonical SI unit**. Silver name: `reading_value_canonical` |
| `canonical_unit` | Unit of `reading_value` |
| `alarm_state` | Alarm level of the reading. KPI logic uses `warning` and `alarm` |
| `alarm_code` | Specific alarm condition. Null when no alarm |
| `is_alarm` | `true` when `alarm_state` is `warning` or `alarm`. `false` otherwise (also when `alarm_state` is null) |
| `batch_id` | Bronze ingestion batch (`_batch_id`) |
| `source_file` | Source file (`_source_file`) |
| `gold_loaded_ts` | Load timestamp |

### 3.2 `gold_fact_maintenance_work_order`

**What it contains:** one row per work order, with keys, timestamps, costs and status flags. Work orders without a valid equipment ID are kept under the Unknown equipment.

**Grain:** one work order. `work_order_id` is unique (checked). **Source:** `silver_work_orders`, enriched with an aggregate of `silver_maintenance_history`.

| Column | Description |
|---|---|
| `work_order_id` | Business key |
| `created_date_key` | FK to `dim_date`, creation day |
| `due_date_key` | FK to `dim_date`, due day |
| `closed_date_key` | FK to `dim_date`, closing day. `-1` if not closed |
| `equipment_key` | FK to `dim_equipment` |
| `site_key` | FK to `dim_site` |
| `equipment_type_key` | FK to `dim_equipment_type` |
| `failure_mode_key` | FK to `dim_failure_mode` |
| `maintenance_type_key` | FK to `dim_maintenance_type` |
| `equipment_id` | Equipment business ID |
| `priority` | Work order priority |
| `status` | Work order status. `Closed` and `Cancelled` are the closed statuses |
| `root_cause_code` | Root cause code |
| `created_ts` | Creation timestamp |
| `due_ts` | Due timestamp |
| `actual_start_ts` | Actual start timestamp |
| `closed_ts` | Closing timestamp |
| `labor_hours` | Labor hours |
| `labor_cost_eur` | Labor cost in EUR |
| `parts_cost_eur` | Parts cost in EUR (decimal 12,2). May be recovered from activities (see `parts_cost_recovered`) |
| `maintenance_cost_eur` | `labor_cost_eur + parts_cost_eur`, nulls treated as 0 |
| `hours_to_close` | Hours from `created_ts` to `closed_ts`. `NULL` for cancelled or not closed |
| `is_open` | `true` when status is not `Closed` or `Cancelled` |
| `is_overdue` | `true` when `is_open` and `due_ts < AS_OF` |
| `parts_cost_recovered` | `true` when the work order is `Closed`, its parts cost was null in Silver (a negative value was nulled), and the parts cost was taken from the maintenance history activities |
| `is_cancelled` | `true` when status is `Cancelled` |
| `is_late_closed` | `true` when status is `Closed` and `closed_ts > due_ts` |
| `activity_count` | Number of maintenance history activities linked to the work order. `0` if none |
| `has_temporary_fix` | `true` when at least one activity has outcome `Temporary fix` |
| `batch_id` | Bronze ingestion batch |
| `source_file` | Source file |
| `gold_loaded_ts` | Load timestamp |

### 3.3 `gold_fact_downtime`

**What it contains:** one row per downtime interval, with overlaps on the same asset removed.

**Grain:** one **non-overlapping** downtime interval per equipment. **Source:** the downtime columns of `silver_work_orders` (rows with `downtime_start_ts` and `downtime_type` set).

**Rules:**
- **Ongoing downtime** (no end timestamp) is counted until `AS_OF`, and flagged `is_ongoing`.
- **Overlap rule:** the interval that starts first wins. A later interval of the same asset starts when the earlier ones have ended. If it lies completely inside an earlier one, it gets 0 hours.
- **Rows without equipment** are never compared with each other (each is treated as its own asset).
- **Repeat failure:** the interval is unplanned, its failure mode is not `NONE`, and the same asset and failure mode had an unplanned failure that ended no more than 30 days before this one started.

| Column | Description |
|---|---|
| `work_order_id` | Work order that carries the downtime |
| `date_key` | FK to `dim_date`, day the clipped interval starts |
| `equipment_key` | FK to `dim_equipment` |
| `site_key` | FK to `dim_site` |
| `equipment_type_key` | FK to `dim_equipment_type` |
| `failure_mode_key` | FK to `dim_failure_mode` |
| `maintenance_type_key` | FK to `dim_maintenance_type` |
| `equipment_id` | Equipment business ID |
| `downtime_type` | `unplanned` or `planned` |
| `downtime_start_ts` | Start **after** overlap clipping |
| `downtime_end_ts` | End **after** overlap clipping |
| `downtime_hours` | Hours between the clipped start and end. **Use this for availability, MTTR and unplanned downtime** |
| `source_start_ts` | Start as delivered, before clipping. The KPI table uses its month to count failures |
| `original_hours` | Hours before clipping. Used for reconciliation with Silver |
| `is_ongoing` | `true` when the source has no end timestamp |
| `is_repeat_failure` | `true` for a repeat failure (rule above) |
| `batch_id` | Bronze ingestion batch |
| `source_file` | Source file |
| `gold_loaded_ts` | Load timestamp |

---

## 4. KPI layer

### 4.1 `gold_kpi_equipment_month` (table)

**What it contains:** the **additive pieces** of every KPI (hours, counts, cost), one row per equipment and month. Because the pieces are additive, they can be summed to any level (site, equipment type, month range, fleet). The KPI is then calculated from the sums.

**Grain:** one equipment per month. **Source:** the three facts, `dim_equipment` and `dim_date`.

**Scope:** the Unknown equipment is excluded. Months run from the first data date up to `AS_OF`. A row exists when the asset had calendar hours in that month or any event in it.

| Column | Description |
|---|---|
| `equipment_key` | FK to `dim_equipment` |
| `site_key` | FK to `dim_site` |
| `equipment_type_key` | FK to `dim_equipment_type` |
| `month_start_date_key` | First day of the month as `yyyyMMdd`. Joins to `dim_date.date_key` (the first day of the month) or to `month_start_date` through that row |
| `calendar_hours` | Hours the asset existed in the month: from `max(month start, data start, install date)` to `min(next month start, AS_OF, decommission date + 1 day)`. `0` or more |
| `asset_days` | `calendar_hours / 24` |
| `operating_hours` | `calendar_hours × operating_share` |
| `downtime_hours` | All downtime hours that fall in the month (planned and unplanned). Intervals crossing a month boundary are split across months |
| `operating_uptime_hours` | `max(0, calendar_hours − downtime_hours) × operating_share` |
| `unplanned_downtime_hours` | Unplanned downtime hours in the month |
| `unplanned_failure_count` | Unplanned downtime intervals that **started** in the month (by `source_start_ts`) |
| `repeat_failure_count` | Of those, how many are repeat failures |
| `alarm_count` | Readings with `is_alarm = true` in the month (warning and alarm) |
| `alarm_warning_count` | Readings with `alarm_state = 'warning'` |
| `alarm_critical_count` | Readings with `alarm_state = 'alarm'` |
| `work_order_count` | Work orders **created** in the month |
| `overdue_work_order_count` | Overdue work orders **due** in the month (overdue measured at `AS_OF`) |
| `maintenance_cost_eur` | Cost of work orders attributed to the month of `closed_ts`, or of `created_ts` if not closed |
| `as_of_ts` | The `AS_OF` timestamp of this run |
| `gold_loaded_ts` | Load timestamp |

`operating_share` is not stored. It is calculated as `(operating_hours_per_day / 24) × (operating_days_per_week / 7)`, with defaults of 24 h and 7 days when missing.

### 4.2 `gold_vw_kpi_equipment_month_v2` (view)

**What it contains:** all KPI formulas applied on top of the table, grouped by `equipment_key`, `site_key`, `equipment_type_key` and `month_start_date_key`. It gives one row per equipment and month with ready-to-read KPI values.

> **Important:** the view is already aggregated. Do not filter it and then average its ratio columns, because that gives wrong results. To get a KPI for a site, a period or the fleet, sum the pieces of the **table** and apply the formula (the notebook's `kpi_query()` does this).

| Column | Formula |
|---|---|
| `availability` | `(SUM(calendar_hours) − SUM(downtime_hours)) / NULLIF(SUM(calendar_hours), 0)` |
| `unplanned_downtime_hours` | `SUM(unplanned_downtime_hours)` |
| `unplanned_failures` | `SUM(unplanned_failure_count)` |
| `mtbf_hours` | `SUM(operating_uptime_hours) / NULLIF(SUM(unplanned_failure_count), 0)` |
| `mttr_hours` | `SUM(unplanned_downtime_hours) / NULLIF(SUM(unplanned_failure_count), 0)` |
| `overdue_work_orders` | `SUM(overdue_work_order_count)` |
| `alarm_rate` | `SUM(alarm_count) / NULLIF(SUM(asset_days), 0)` |
| `alarm_warning_rate` | `SUM(alarm_warning_count) / NULLIF(SUM(asset_days), 0)` |
| `alarm_critical_rate` | `SUM(alarm_critical_count) / NULLIF(SUM(asset_days), 0)` |
| `maintenance_cost_eur` | `SUM(maintenance_cost_eur)` |
| `repeat_failure_rate` | `SUM(repeat_failure_count) / NULLIF(SUM(unplanned_failure_count), 0)` |
| `work_orders` | `SUM(work_order_count)` |

A zero denominator returns `NULL`, not 0.

---

## 5. Relationships

| From (many side) | Column | To (one side) | Column |
|---|---|---|---|
| `dim_equipment` | `site_key` | `dim_site` | `site_key` |
| `dim_equipment` | `equipment_type_key` | `dim_equipment_type` | `equipment_type_key` |
| `fact_sensor_reading` | `date_key` | `dim_date` | `date_key` |
| `fact_sensor_reading` | `equipment_key` | `dim_equipment` | `equipment_key` |
| `fact_sensor_reading` | `site_key` | `dim_site` | `site_key` |
| `fact_sensor_reading` | `equipment_type_key` | `dim_equipment_type` | `equipment_type_key` |
| `fact_maintenance_work_order` | `created_date_key` / `due_date_key` / `closed_date_key` | `dim_date` | `date_key` (role-playing, three relationships) |
| `fact_maintenance_work_order` | `equipment_key`, `site_key`, `equipment_type_key` | the three equipment dimensions | matching keys |
| `fact_maintenance_work_order` | `failure_mode_key` | `dim_failure_mode` | `failure_mode_key` |
| `fact_maintenance_work_order` | `maintenance_type_key` | `dim_maintenance_type` | `maintenance_type_key` |
| `fact_downtime` | `date_key` | `dim_date` | `date_key` |
| `fact_downtime` | `equipment_key`, `site_key`, `equipment_type_key` | the three equipment dimensions | matching keys |
| `fact_downtime` | `failure_mode_key` | `dim_failure_mode` | `failure_mode_key` |
| `fact_downtime` | `maintenance_type_key` | `dim_maintenance_type` | `maintenance_type_key` |
| `fact_downtime` | `work_order_id` | `fact_maintenance_work_order` | `work_order_id` |
| `kpi_equipment_month` | `equipment_key`, `site_key`, `equipment_type_key` | the three equipment dimensions | matching keys |
| `kpi_equipment_month` | `month_start_date_key` | `dim_date` | `date_key` (first day of month) |

---

## 6. Built-in reconciliation checks

The notebook prints PASS or FAIL for each check and stops (`assert`) if any fails.

| Cell | Check |
|---|---|
| Dimensions | Every surrogate key is unique (catches hash collisions) |
| Dimensions | `dim_equipment` rows = distinct Silver equipment IDs + the Unknown row |
| Facts | Sensor rows = distinct `(equipment, sensor, timestamp)` in Silver |
| Facts | Work order rows = Silver work order rows, and `work_order_id` is unique |
| Facts | Downtime rows = valid Silver downtime rows |
| Facts | Downtime hours before clipping (`original_hours`) = Silver downtime hours |
| Facts | Maintenance cost EUR = Silver cost + recovered parts cost |
| Facts | No overlapping downtime intervals for the same asset |
| Facts | Every key in every fact exists in its dimension (14 key checks) |
| KPI | Sums of the KPI table pieces equal the fact totals: downtime hours, unplanned downtime hours, unplanned failures, repeat failures, alarms, work orders, overdue work orders, cost |

---

## 7. Things to know when using these tables

1. **Time grain of KPIs is the month.** The KPI table cannot answer day-level questions. Use the facts for that.
2. **Overdue is a snapshot.** `is_overdue` is evaluated at `AS_OF`, and `overdue_work_order_count` is bucketed by the **due month**. It is not a point-in-time history.
3. **Alarm rates count readings, not events.** A timestamp where two sensors alarm counts twice. The rate also grows with the number of sensors per asset.
4. **Cost timing.** Cost is bucketed by `closed_ts`, or `created_ts` when still open, so open work orders already contribute cost.
5. **Failures and downtime hours use different month logic.** Failures count in the month the interval started. Downtime hours are split across months. Monthly MTTR can be slightly distorted near month boundaries.
6. **MTBF uses `operating_share`.** The formula is `operating_uptime_hours / failures`, not `(calendar hours − downtime) / failures`. Confirm this against the agreed KPI definition.
7. **`fact_downtime` is built from work orders**, not from `silver_maintenance_history`.
8. **Equipment history is not kept.** `dim_equipment` is SCD type 1, so site transfers or criticality changes overwrite the old values.
9. **Unknown equipment** appears in the facts (key `-1`) but is excluded from the KPI table, so KPI totals can be lower than fact totals when orphan rows exist.
10. **Exact column types** for pass-through columns come from Silver. Run `DESCRIBE TABLE` on a Gold table to confirm them.

---

## 8. Knowledge graph tables

Two extra Gold tables describe the plant as a graph. They are built in Databricks **after** the
KPI table, **only from the Gold tables above** (nothing is inferred), with a full overwrite and `gold_loaded_ts` like the other Gold tables. The assistant
tool `query_graph` reads them (through Neo4j or an in-memory copy, see `src/app/agent/tools/README.md`).

### 8.1 `gold_kg_nodes`

**What it contains:** one row per node: a dimension member, a sensor or a work order.

**Grain:** one node. **Key:** `node_id` (unique, checked).

| Column | Description |
|---|---|
| `node_id` | `<node_type>:<business_key>`, for example `Equipment:EQ-10001`, `FailureMode:BRG-WEAR`. Business keys, not surrogate keys, so a user or a tool can type them |
| `node_type` | `Site`, `EquipmentType`, `Equipment`, `Sensor`, `FailureMode`, `MaintenanceType` or `WorkOrder` |
| `business_key` | `site_code`, `equipment_type`, `equipment_id`, `sensor_id`, `failure_mode_code`, `maintenance_type` or `work_order_id` |
| `label` | Readable name: site name, equipment name, failure mode description; the business key when there is none |
| `properties` | JSON string with the attributes of the node type (table below). Null attributes are left out |
| `source_table` | Gold table the node comes from |
| `gold_loaded_ts` | Load timestamp |

| `node_type` | Source | Attributes in `properties` |
|---|---|---|
| `Site` | `dim_site` | `country`, `timezone` |
| `EquipmentType` | `dim_equipment_type` | none |
| `Equipment` | `dim_equipment` | `manufacturer`, `model_number`, `production_line`, `criticality`, `status`, `install_date` |
| `Sensor` | `fact_sensor_reading`, one node per `sensor_id` | `sensor_type`, `reading_count`, `alarm_count`, `last_reading_ts` |
| `FailureMode` | `dim_failure_mode` | none |
| `MaintenanceType` | `dim_maintenance_type` | `is_planned` |
| `WorkOrder` | `fact_maintenance_work_order` (joined to `fact_downtime` per work order) | `priority`, `status`, `root_cause_code`, `created_ts`, `closed_ts`, `maintenance_cost_eur`, `is_overdue`, `has_temporary_fix`, `downtime_type`, `downtime_hours` |

### 8.2 `gold_kg_edges`

**What it contains:** one row per directed edge between two nodes.

**Grain:** one edge per (`src_id`, `dst_id`, `edge_type`).

| Column | Description |
|---|---|
| `src_id` | `node_id` of the node the edge starts at |
| `dst_id` | `node_id` of the node the edge points to |
| `edge_type` | Type of the relation (table below) |
| `weight` | `1` for links that follow a key; a count for the aggregated edges; a gap in days (rounded to 2 decimals) for `REPEATED_FAILURE` and `FOLLOWED_BY` |
| `properties` | JSON string with extra attributes of the edge type (table below), or null |
| `source_table` | Gold table the edge comes from |
| `gold_loaded_ts` | Load timestamp |

| `edge_type` | From -> to | Meaning, `weight` and `properties` |
|---|---|---|
| `LOCATED_AT` | Equipment -> Site | The asset's site (`dim_equipment.site_key`) |
| `IS_OF_TYPE` | Equipment -> EquipmentType | The asset's type (`dim_equipment.equipment_type_key`) |
| `HAS_SENSOR` | Equipment -> Sensor | The sensor reported readings for the asset |
| `PERFORMED_ON` | WorkOrder -> Equipment | The work order is for this asset. Work orders without a known asset get no edge |
| `HAS_FAILURE_MODE` | WorkOrder -> FailureMode | Failure mode of the work order. No edge for `NONE` (planned work) or an unknown mode |
| `HAS_MAINTENANCE_TYPE` | WorkOrder -> MaintenanceType | Maintenance type of the work order |
| `REPEATED_FAILURE` | WorkOrder -> WorkOrder | A repeat failure (Gold's `is_repeat_failure`) -> the earlier failure of the same asset and mode. `weight` = days from the end of the earlier failure to the start of this one. `properties`: `failure_mode` |
| `FOLLOWED_BY` | WorkOrder -> WorkOrder | An unplanned failure -> the next unplanned failure of the same asset, in any mode. `weight` = days between them. `properties`: `from_mode`, `to_mode` |
| `EXPERIENCED_FAILURE` | Equipment -> FailureMode | `weight` = number of unplanned failures of that asset and mode. `properties`: `downtime_hours`, `repeat_failures`, `last_failure_ts` |
| `TYPE_PRONE_TO` | EquipmentType -> FailureMode | `weight` = number of unplanned failures of that type and mode. `properties`: `equipment_affected`, `downtime_hours` |

### 8.3 Rules

- **Left out:** the Unknown members (key `-1`) of every dimension and the failure mode `NONE`, so planned work does not become a hub that links everything.
- **Failures** (basis of the four derived edge types) are the rows of `fact_downtime` with `downtime_type = 'unplanned'`, a known asset and a real failure mode. This is the population of the KPI failures, so graph counts and KPI counts agree.
- Sensor readings are **not** nodes, only per-sensor aggregates (`Sensor` attributes), so the graph stays small.
- `source_table` carries the table name as it is in the workspace, with the `test_` prefix in the test notebook.
- The `properties` column is JSON text, so the tables stay flat; the loaders turn it into plain properties.

### 8.4 Reconciliation checks

These hold for the delivered tables (checked read-only against the Gold tables); repeat them after every rebuild.

| Check |
|---|
| `node_id` is unique; no edge points to a source or target node that does not exist |
| Node counts equal the dimension counts without the Unknown row (and without `NONE` for failure modes); `WorkOrder` nodes = rows of `fact_maintenance_work_order`; `Sensor` nodes = distinct sensors |
| `REPEATED_FAILURE` edges = failures flagged `is_repeat_failure` |
| `FOLLOWED_BY` edges = failures minus assets with failures (each asset's first failure has no predecessor) |
| Sum of `weight` of `EXPERIENCED_FAILURE` edges = number of failures; same for `TYPE_PRONE_TO` |

### 8.5 Things to know

1. **Counts have no period.** The graph covers all data in Gold. Use the KPI layer for figures by month.
2. **`FOLLOWED_BY` shows a sequence, not a cause.** It links consecutive failures of one asset.
3. **Same SCD limit as `dim_equipment`.** Attributes are the latest values; a site transfer overwrites the old one.
4. **A copy is kept outside Databricks** for the assistant (Neo4j, or in memory), refreshed from these tables when the API starts and Gold has a newer `gold_loaded_ts`.
