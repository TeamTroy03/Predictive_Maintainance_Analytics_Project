# Silver rules: `maintenance_history`

How to clean `maintenance_history_2.csv` from Bronze to Silver in a Databricks notebook.

| | |
|---|---|
| Team | 3, Predictive Maintenance Analytics |
| Owner | P1 Data engineering |
| Input | `bronze_maintenance_history` (batches `BATCH-20260915-01` and `BATCH-20260922-02`) |
| Output | `silver_maintenance_history`, plus records in `silver_dq_quarantine`, `dq_results`, `silver_dq_reconciliation` |
| Depends on | `silver_work_orders` (6 columns, section 3.1) and the `equipment_id` list of `silver_equipment` |
| Code | `databricks/silver/silver-max/mh_silver_logic.py`, tests in `test_mh_silver_logic.py` |
| Based on | Profiling note `maintenance_history_bronze_to_silver.pdf` (2026-10-02) and its re-verification (2026-10-03) |
| Status | Draft. Items marked **OPEN** need agreement (section 13) |

Table names are the code defaults. Catalog and schema come from notebook widgets (`catalog`, `schema`), never from code.

> **How to read the counts.** *Raw* means the 10,000 Bronze rows. *After dedup* means the 9,800 rows left once exact duplicates are removed and only the latest `record_version` per `maintenance_id` is kept. Every rule runs on those 9,800 rows. The counts are the **defect manifest**: the test script (`MANIFEST` in `test_mh_silver_logic.py`) asserts them. If a run gives a different number, find out whether the code or the data changed. Never edit the number to make a test pass.

---

## 1. What Silver does, and what it doesn't

**Team rule (record it in an ADR): Silver holds cleaned data and the evidence of the cleaning. Status logic, measures, labels, lifecycle checks and aggregation live in Gold.**

A calculation belongs in Silver only if you need it to find or fix a bad value. Ask of every new column: *does it describe the cleaning (what was wrong, what we did), or the business?* Only the first kind stays here.

| Silver (this document) | Gold (not here) |
|---|---|
| Types, UTC timestamps, trimmed strings, code checks | Costs: `labor_cost_eur`, `activity_cost_eur`, maintenance cost |
| Exact duplicates and record versions | Work categories (planned PM, planned inspection, failure) |
| Recovering missing or wrong values from the work order | Labels such as "Not applicable (planned work)" |
| Flags for problems we can't fix (unregistered equipment, root-cause conflicts) | Resolving root-cause conflicts between activity and work order |
| Quarantine, rule counts, row-flow reconciliation | Lifecycle check (activity outside the asset's install/decommission dates) |
| | Labour and parts sums against work orders, legacy totals (section 8.2) |
| | Reporting period, legacy filter, Unknown members (−1), KPIs |

Two examples of the boundary:

- **Labour hours.** Silver works out the ratio `labor_hours ÷ duration` to detect values booked in minutes, then fixes them. The ratio is a cleaning tool, so it is computed during the run and **not stored**. Gold later multiplies the cleaned hours by 55 EUR/h; that is a measure.
- **Expected nulls.** `root_cause_code` is null for planned work. Silver checks that the null is valid (rule RC_NULL_IFF_NONE) and leaves it null. The label "Not applicable" is added in Gold.

**Schema note.** Project instructions §4.3 describe `maintenance_history` as downtime records. The delivered file is one row per maintenance **activity**, with no downtime columns. Downtime lives on `work_orders`, so `fact_downtime` will come from `silver_work_orders`. Record this in an ADR.

---

## 2. Source

### 2.1 Batches

| `batch_id` | `source_file` | `source_system` | Rows |
|---|---|---|---|
| BATCH-20260915-01 | `cmms_maintenance_history_extract_20260915.csv` | CMMS | 9,545 |
| BATCH-20260915-01 | `legacy_cmms_history_export_20260915.csv` | LEGACY_CMMS | 50 |
| BATCH-20260922-02 | `cmms_maintenance_history_extract_20260922.csv` | CMMS | 405 |
| **Total** | | | **10,000** |

- **CMMS** is the current maintenance system. Its records start at go-live, 2026-03-15.
- **LEGACY_CMMS** is the previous system: 50 failure repairs from 2025-10-06 to 2026-03-08. They have **no `work_order_id`**, because work orders only exist in the current CMMS. That is valid, not a defect.
- **Batch 2** (405 rows) holds 303 new activities, 100 corrected versions (`record_version = 2`) of batch-1 activities, and 2 exact duplicates. 10 of the new activities started before the batch-1 extract (late-arriving).
- The 100 exact duplicates sit **inside one file** each time (98 in batch 1, 2 in batch 2) and are identical on every column, lineage included.

### 2.2 What Silver reads from Bronze

The 25 source columns as delivered, plus the Bronze audit columns. The team's Bronze notebook names them differently; the code renames them on read (`adapt_bronze_columns`, parameter `bronze_column_map`):

| Bronze column | Name in this notebook |
|---|---|
| `batch_id` (Bronze micro-batch number) | `_batch_id` |
| `source_batch_id` (the source's `batch_id`) | `batch_id` |
| `ingestion_timestamp` | `_ingestion_ts` |
| `source_filename` | `_source_file` |
| `record_hash` | `_record_hash` |
| `load_status` | `_load_status` |
| `source_system` (copied) | `_source_system` |

Silver reads only rows where `_load_status` equals `bronze_loaded_status` (default `SUCCESS`). Rows with another status (e.g. `RESCUED`) stay in Bronze; the run prints how many. If no row matches, the run stops: a wrong status filter would otherwise MERGE-delete all of Silver.

The code works whether Bronze stores the source columns as strings or already typed.

| Columns | Raw format | Empty means |
|---|---|---|
| `activity_start_ts`, `activity_end_ts`, `last_modified_ts`, `ingestion_ts` | `yyyy-MM-ddTHH:mm:ssZ` (UTC) | never empty |
| `next_due_date` | `yyyy-MM-dd` | not applicable (failure work) |
| `parts_cost_eur`, `labor_hours` | decimal, ≤ 2 places, can be negative | never empty |
| `parts_qty`, `record_version` | integer (`parts_qty` can be negative) | never empty |
| `work_order_id` | `WO-nnnnn` | legacy record (valid) |
| `equipment_id` | `EQ-nnnnn` | defect |
| `root_cause_code` | code | not applicable (planned work) |

> **Check with the Bronze owner.** The 100 in-file duplicates share the same `_record_hash`. If Bronze deduplicates on the hash, they never reach Silver, DUP_EXACT counts 0 and the row flow stops matching the source file. Bronze idempotency should work per batch (skip or replace a batch already loaded), not per row hash.

---

## 3. References

This notebook runs **after** the work-orders and equipment Silver notebooks and reads both from Silver only (there is no Bronze fallback). It checks their columns at the start (SCHEMA_CONTRACT) and stops with a message listing every missing column. A missing table fails on read.

### 3.1 Contract with `silver_work_orders`

Only these 6 columns are read, as a lookup per work order:

| Column | Type | Contract | Used by |
|---|---|---|---|
| `work_order_id` | string | Unique: latest version only | join key |
| `equipment_id` | string | Null when missing in the source | EQ_RECOVERED_FROM_WO, EQ_MATCHES_WO |
| `failure_mode_code` | string | As in the source (latest version) | FM_MATCHES_WO |
| `root_cause_code` | string | As in the source (latest version) | RC_CONFLICT_WITH_WO |
| `labor_hours` | decimal(8,2) | **Null when invalid** (9999, negative) or not yet closed | L4 |
| `parts_cost_eur` | decimal(12,2) | Parts cost in EUR. **Null when invalid** (negative) or when it can't be converted | C2 |

**Why "null when invalid" matters.** This notebook recovers bad activity values from the work order (rules C2 and L4). The work-orders notebook recovers bad work-order values from the activities. If both sides recovered from each other's *bad* values, each would depend on the other. So: work orders hand over invalid values as null, this notebook recovers from valid values only, and the work-orders owner later fills their nulls from the clean activity sums (`GROUP BY work_order_id` on `silver_maintenance_history`).

Currency conversion is the work-orders owner's job: this notebook never converts currencies, it only reads `parts_cost_eur`.

### 3.2 Contract with `silver_equipment`

Only `equipment_id` is read (string, not null). The distinct list of IDs is the equipment master for EQ_UNREGISTERED. SCD2 versions, dates and defect flags are not read here; the lifecycle check that needs them is in Gold.

---

## 4. Processing order

Each step relies on the one before it. Function names are from `mh_silver_logic.py`.

| Step | What | Function | Rules | Section |
|---|---|---|---|---|
| 0 | Read Bronze and the two references; check their columns | `check_contracts` | SCHEMA_CONTRACT | 2.2, 3 |
| 1 | Standardise: trim, empty → null, cast | `standardise` | TYPE_CAST | 5.1 |
| 2 | Flag exact duplicates (before any sum, so nothing counts twice) | `deduplicate` | DUP_EXACT | 5.2 |
| 3 | Flag all but the latest version per `maintenance_id` | `deduplicate` | VERSION_LATEST | 5.2 |
| 4 | Attach the work order; resolve the equipment key | `resolve_work_order_and_equipment` | WO_*, EQ_* | 5.3, 5.4 |
| 5 | Fix timestamps (step 6 needs a valid duration) | `fix_timestamps` | TS_*, NDD_AFTER_END | 5.5 |
| 6 | Fix parts cost, then parts quantity; fix labour hours | `fix_parts`, `fix_labour` | C1–C3, Q1–Q2, L1–L4 | 5.6, 5.7 |
| 7 | Check consistency with the work order and the code lists | `check_rules` | section 7 | 5.8, 5.9 |
| 8 | Build Silver, quarantine, rule counts, row flow; collect error counts | `build_silver` | | 6, 8 |
| 9 | Stop if an error rule fired; otherwise MERGE Silver, MERGE quarantine, append DQ results and row flow | `main` | | 6 |

Every rule sets a boolean working column `_f_<RULE>`. Counting, the error gate and the DQ results read those columns, so **adding a rule** means one line in `RULES` (severity, dimension, description) and one `_f_<RULE>` column in a step.

---

## 5. Rules

### 5.1 Standardise

- Set the Spark session time zone to UTC. Parse timestamps with the pattern `yyyy-MM-dd'T'HH:mm:ss'Z'`; parse `next_due_date` as a date.
- Trim every string; turn empty strings into null. Don't change case: codes already arrive in canonical case, and DOMAIN_CODES fails the run if that changes.
- `parts_cost_eur` → `decimal(12,2)`, `labor_hours` → `decimal(8,2)`, `parts_qty` and `record_version` → `int`. **Never `double` for money or hours**: totals must reconcile to the cent.
- Parse without throwing (`try_to_timestamp`, `try_cast`). A non-empty value that doesn't parse counts under **TYPE_CAST** and fails the run: the source format changed.
- **No raw copies in Silver.** The original values stay in Bronze. Silver keeps `_record_hash`, `_batch_id` and `record_version`, so any Silver row can be joined back to its exact Bronze row (query in 6.5). The `*_fix_rule` columns say which rows changed and why. (The delivered row is kept as a working value during the run, only to fill `original_values` in quarantine records.)

### 5.2 Duplicates and versions

| Check | Rows |
|---|---|
| Exact duplicate rows | 100 |
| Same `maintenance_id` and version, different content | 0 |
| Older version superseded by a newer one | 100 |

- **DUP_EXACT.** Hash the 20 business columns (all source columns except `source_file`, `ingestion_ts`, `batch_id`, `source_system`, `source_system_key`). Same hash = duplicate. Keep the row with the earliest `_ingestion_ts`, then `_source_file`, then `_record_hash`. Leaving lineage out of the hash also catches an identical row re-sent in a later batch.
- **VERSION_LATEST.** Keep the highest `record_version` per `maintenance_id`; tie-break on `last_modified_ts` descending, then `_ingestion_ts` descending, then `_record_hash`. No ties today; the tie-breaks are defensive and make reruns pick the same row.

What the 100 corrections change: `action_description` (47), `technician_id` (37), `root_cause_code` (10), `component` + `component_code` (6, always BRG-DE → BRG-NDE). No correction touches equipment, timestamps, cost, labour, failure mode or outcome. Corrections arrive 7 to 181 days after the original, so the notebook rebuilds from **all** Bronze rows each run (section 6.1).

### 5.3 Expected nulls and the work order

| Column | Nulls after dedup | Verdict |
|---|---|---|
| `work_order_id` | 50 | Valid: all LEGACY_CMMS |
| `equipment_id` | 42 | Defect (5.4) |
| `root_cause_code` | 5,337 | Valid: planned work has no failure |
| `next_due_date` | 4,463 | Valid: failure work has no next planned date |

- **Keep these nulls. Never fill them with placeholders** such as `INSP`, `MAIN` or `"NO"`. A placeholder mixes two meanings in one column, would outnumber every real root cause in charts, and in `next_due_date` would turn a date column into text.
- Validate them instead: **RC_NULL_IFF_NONE** (root cause is null exactly when `failure_mode_code = NONE`) and **NDD_IFF_PLANNED** (next due date is set exactly when `failure_mode_code = NONE`). Both are error rules with 0 violations.
- `detection_method = inspection` covers both planned inspections and inspections after a breakdown. Never use it to decide whether work was planned; use `failure_mode_code`.
- **WO_NULL_NON_LEGACY** (error, 0): a null `work_order_id` is valid only when `source_system = LEGACY_CMMS`.
- **WO_ORPHAN** (warn, 0): a `work_order_id` that isn't in the work-orders table. The recovery rules can't use such rows (a work order may arrive late).
- **Legacy rows stay in Silver.** Choosing the reporting period is a Gold decision. Don't filter them out at ingestion or here; 50 source rows would vanish without explanation.

### 5.4 Equipment key

**Missing `equipment_id` (42 rows)**

- When both have a value, the activity's `equipment_id` always equals its work order's (EQ_MATCHES_WO, error, 0). So the work order is a reliable recovery source.
- **EQ_RECOVERED_FROM_WO (fix, 32 CMMS rows).** Take `equipment_id` from the work order; `equipment_resolution = RECOVERED_FROM_WO`.
- **EQ_MISSING_UNRECOVERABLE (row quarantine, 10 LEGACY_CMMS rows).** No work order, nothing to recover from. The row goes to quarantine only, not to Silver. (The data dictionary says these belong to work orders that are also missing the ID. That is wrong: they have no work order at all. Fix the dictionary.)

**Unregistered equipment (58 CMMS rows)**

- **EQ_UNREGISTERED (flag).** Detect by lookup: the (resolved) `equipment_id` isn't in the `silver_equipment` ID list. Today all 58 are `EQ-9999`, but **never hard-code `EQ-9999`**.
- Keep the source value; don't null it. "Unregistered" and "missing" are different problems for the master-data team.
- `dq_unknown_equipment = true`, `equipment_resolution = UNREGISTERED`.
- A recovered ID that is also unregistered keeps `equipment_resolution = RECOVERED_FROM_WO`, so the change stays recorded; `dq_unknown_equipment` still shows the lookup miss.

`equipment_resolution` values in Silver: `SOURCE`, `RECOVERED_FROM_WO`, `UNREGISTERED`.

**Lifecycle check: moved to Gold.** Whether an activity falls between the asset's `install_date` and `decommission_date` (site-local date, SCD2 version, equipment defect flags) only ever warned and never changed a value. It needs the full equipment table, so it is done in Gold. Expected findings for the Gold owner: 53 conflicts (44 on assets with an install-date defect, 9 planned activities 1–20 days after decommission; **OPEN**, section 13).

### 5.5 Activity timestamps (TS_END_BEFORE_START, field quarantine, 100)

100 rows have `activity_end_ts` before `activity_start_ts`. The work order shows which value is wrong:

- **The start is right.** In 93 of the 100 rows it equals the work order's `actual_start_ts` to the second; the other 7 are the second activity on a two-activity work order.
- **The end is wrong.** In 98 of the 100 it is even earlier than when the work order says work began.

Rule: keep the start, set `activity_end_ts` to null (the original stays in Bronze and in the quarantine record's `original_values`), write a field-quarantine record. The row stays in Silver: its labour, cost and failure data are valid.

- **Don't swap start and end.** That moves the verified start into the end column and makes the bad value the new start.
- **Don't estimate the end** as start + labour hours: you don't know whether one or two people worked.

Extra checks, all warn, 0 today: **TS_AFTER_EXTRACT** (start or end after the row's `ingestion_ts`), **TS_MODIFIED_BEFORE_END** (`last_modified_ts` < `activity_end_ts`, valid ends only), **NDD_AFTER_END** (`next_due_date` not after the activity's end date; start date when the end is null).

### 5.6 Parts cost and quantity

Why `abs()` is wrong for cost:

- 13 rows are −5.00 on Inspect, Calibrate or Adjust activities. These activity types never use parts, so the true value is 0. `abs()` would invent €5 of parts on an inspection.
- 8 rows are on parts activities and the size is wrong as well as the sign. Example: MH-00315 shows −175.13, but its work order has `parts_cost` 170.13 and no other activity. `abs()` overstates all 8.
- One (MH-03785) sits on a GBP work order, so it recovers only through the work order's EUR value (`parts_cost_eur`).

> **Domain assumption (C1).** Inspect, Align, Adjust and Calibrate activities never use parts (parameter `no_parts_activity_types`). C1 changes values based on this business knowledge; if the assumption changes, change the parameter, not the code.

**Cost rules, in order** (recorded in `parts_fix_rule`):

| Rule | `parts_fix_rule` | Logic | Rows |
|---|---|---|---|
| C1 | `NO_PARTS_ACTIVITY_COST_ZEROED` | `activity_type` in `no_parts_activity_types` and cost ≠ 0 → 0 | 13 |
| C2 | `NEG_COST_RECOVERED_FROM_WO` | Cost < 0 on a parts activity → work order `parts_cost_eur` − sum of the **other** activities' cost on that work order (after C1). Only when exactly one activity on the work order is bad, the work-order value is not null and the result is ≥ 0 | 8 |
| C3 | `NEG_COST_UNRECOVERABLE` | C2 not possible (no work order, work-order value null, two bad activities, result < 0) → null and a field-quarantine record. **Never `abs()` a leftover cost** | 0 |

Never write "add 5" or "subtract 10" logic, even though the data shows fixed offsets: that is how the defects were generated, not a business rule.

**Quantity rules, after the cost rules:**

| Rule | `parts_fix_rule` | Logic | Rows |
|---|---|---|---|
| Q1 | `ZERO_COST_QTY_ZEROED` | Cleaned cost = 0 and quantity ≠ 0 → 0 | 10 |
| Q2 | `NEG_QTY_CORRECTED` | Quantity < 0 with a real cost → `abs()`. An inferred fix: no second source holds a quantity | 15 |

`parts_fix_rule` lists every applied rule joined with `;` (e.g. `NO_PARTS_ACTIVITY_COST_ZEROED;ZERO_COST_QTY_ZEROED`), or `UNCHANGED`.

After the fixes: **PARTS_QTY_COST_CONSISTENT** (error, 0): quantity is 0 exactly when cost is 0 (rows with null cost are skipped).

### 5.7 Labour hours

Working values, computed during the run and **not stored**:

```
duration_h = (activity_end_ts − activity_start_ts) in hours     null when the end is null
ratio      = |labor_hours| ÷ max(duration_h, 0.25)
factor     = round(ratio)      valid when factor ∈ {1, 2} and |ratio − factor| ≤ 0.05
```

The 0.25 h floor is the minimum booking: a 6-minute job booked as 0.25 h is factor 1, not 2.5. A row without a duration never has a valid factor.

> **Domain assumption (L2).** One or two people work on an activity (parameter `allowed_factors = [1, 2]`), so booked hours are 1× or 2× the duration. L2 converts values based on this assumption. Factor 2 probably means two technicians, but that is unconfirmed (**OPEN**), so the factor isn't stored.

**Rules, in this order** (recorded in `labor_fix_rule`):

| Rule | `labor_fix_rule` | Logic | Rows |
|---|---|---|---|
| L1 | (interim) | Value in `labour_sentinels` (999) → null; recovered in L4 | 22 |
| L2 | `MINUTES_TO_HOURS` | `ratio ÷ 60` is a valid factor (1 or 2, ±0.05) → divide by 60 | 100 |
| L3 | `NEGATIVE_ABS` | Negative → `abs()`. All 32 then match their work order, which verifies the fix | 32 |
| L4 | `SENTINEL_999_RECOVERED` | Work order's `labor_hours` − sum of its other activities' fixed hours. Only when exactly one sentinel is on the work order, the work-order value is not null and the result is > 0 | 22 |
| L4 | `LABOR_HOURS_UNRECOVERABLE` | Recovery impossible (no work order, work-order labour null, two sentinels, result ≤ 0) → null and a field-quarantine record. Re-checked every run: the work order may close later | 0 |
| | `UNCHANGED` | Everything else, including factor-2 rows and 0.25 h / 0.5 h minimum bookings | 9,646 |

**Detect minutes by ratio, never with a "> 60" threshold.** 5 genuine values exceed 60 h (e.g. MH-00160 = 64.31 h on a long activity) and 10 minute values are below 60. The bands are clear: 85 rows have `ratio ÷ 60` between 0.993 and 1.005, 15 between 1.998 and 2.001, and no other row is above 0.034.

The 100 rows with an invalid end have no duration, so L2 can't run on them. They keep their labour: 99 match their work order directly and the last matches once its sibling is fixed.

**LABOR_UNEXPLAINED_RATIO** (error, 0): after L1–L4, every row with a duration and labour hours has a valid factor. A failure means a new kind of defect nobody has handled, so the run stops.

### 5.8 Consistency with the work order

- **FM_MATCHES_WO** (error, 0): the activity's `failure_mode_code` equals its work order's.
- **RC_CONFLICT_WITH_WO** (warn, 10): root cause differs from the work order's. The 10 root-cause corrections arrived only for activities; their work orders still hold the old code. Silver sets `dq_rc_conflict_with_wo = true` and keeps each table's own value. **Deciding which value wins is Gold's job** (proposal: the most recently modified record wins; **OPEN**, ADR with the work-orders owner).
- **ACTIVITIES_PER_WO** (warn, 0): more than `max_activities_per_work_order` (2) activities on one work order. A business expectation rather than a data defect, so it only warns.

### 5.9 Code checks

- **DOMAIN_CODES** (error, 0): `activity_type`, `component_code`, `failure_mode_code`, `root_cause_code` (when set), `detection_method`, `outcome` are in their code lists (section 10); `technician_id` matches `TECH-nn`.
- **COMPONENT_CODE_MAP** (error, 0): `component` maps to `component_code` as in section 10 (`Bearing and coupling` → `BRG-DE`).
- **SOURCE_KEY_FORMAT** (error, 0): `source_system_key = source_system || '|' || maintenance_id`.

---

## 6. Writing the outputs

### 6.1 Refresh pattern: rebuild from all Bronze, MERGE into Silver

Each run rebuilds the cleaned set from **all** Bronze rows of this entity, then MERGEs it into Silver. Processing only the new batch would be wrong: C2 and L4 compare sibling activities that can arrive in different batches, corrections arrive months later, and L4 must re-check every run. At 10,000 rows a rebuild takes seconds. (Record this in an ADR.)

MERGE on `maintenance_id`:

| Condition | Action |
|---|---|
| Matched, incoming `record_version` > target | Update all columns |
| Matched, same version, `_silver_row_hash` differs | Update all columns (a rule outcome changed, e.g. L4 recovered) |
| Matched, otherwise | Nothing |
| Not matched (new row) | Insert |
| In Silver but not in the rebuilt set (now row-quarantined) | Delete (0 expected) |

`_silver_row_hash` = SHA-256 over the business, fix-rule and flag columns, with nulls replaced by a fixed marker before hashing. It excludes lineage and run metadata. So **a rerun on unchanged data inserts, updates and deletes 0 rows**: that is your idempotency evidence (the run prints the three numbers).

The table is created on the first run (`CREATE TABLE IF NOT EXISTS`, column comments from section 9). If an existing table's columns differ from section 9 (e.g. an older version with the lifecycle columns), the run stops and asks you to `DROP TABLE silver_maintenance_history` and rerun.

### 6.2 Error gate

Error rules are invariants. Count them **before** any write. If any is above 0: append the DQ results, then stop the notebook with an error naming each rule and its count. Silver and quarantine are not touched.

### 6.3 Quarantine pattern (one pattern for the whole team, ADR)

- **Row quarantine:** the row is unusable (no equipment key at all). It goes to quarantine only.
- **Field quarantine:** one field is wrong. The row stays in Silver with that field null, and a quarantine record is written as well.
- Fixes, flags and warnings write **no** quarantine records. Their evidence is the `*_fix_rule` columns, the flags, the rule counts and the original values in Bronze.

| Rule | Type | Field set to null |
|---|---|---|
| EQ_MISSING_UNRECOVERABLE | ROW | (whole row) |
| TS_END_BEFORE_START | FIELD | `activity_end_ts` |
| C3 | FIELD | `parts_cost_eur` |
| L4_UNRECOVERABLE | FIELD | `labor_hours` |

`silver_dq_quarantine` (shared by all entities):

| Column | Type | Meaning |
|---|---|---|
| `entity` | string | `maintenance_history` |
| `record_id` | string | `maintenance_id` |
| `rule_id` | string | Rule that fired |
| `quarantine_type` | string | `ROW` or `FIELD` |
| `field_name` | string | Field set to null (FIELD only) |
| `reason` | string | Readable reason (the rule's description) |
| `original_values` | string (JSON) | Raw value of the affected field; the whole source row for ROW |
| `record_version` | int | Version the rule fired on |
| `_batch_id`, `_source_file` | string | Bronze lineage |
| `quarantine_ts` | timestamp | First time the rule fired for this record |
| `last_seen_ts`, `last_run_id` | timestamp, string | Last run it still fired |
| `resolution_status` | string | `OPEN` or `RESOLVED` |
| `resolved_ts` | timestamp | When it stopped firing |

MERGE on (`entity`, `record_id`, `rule_id`), **never append**, so reruns create no duplicates. A record that no longer fires becomes `RESOLVED` (only rows of this entity). Expected open records today: **110** (10 ROW + 100 FIELD).

### 6.4 DQ results

The notebook shows the rule counts per rule and source batch, plus a total with `batch_id = 'ALL'` (raw rules counted on the 10,000 Bronze rows, the rest on the 9,800 deduplicated rows).

The totals are appended to the team's shared `dq_results` table (append-only), one row per run and rule: `table_name` (`silver_maintenance_history`), `rule_name`, `dimension`, `severity`, `description`, `records_evaluated`, `records_failed`, `failure_rate`, `run_ts`, `run_id`. Counts go into this table, not only into notebook output. Severity, dimension and description come from `RULES` in the code.

### 6.5 Seeing the original values

Silver doesn't copy the source values. To show before and after for any fixed row (good demo evidence), join back to Bronze on the row hash (the notebook runs this at the end):

```sql
SELECT s.maintenance_id,
       s.parts_fix_rule, b.parts_cost_eur AS parts_cost_before, s.parts_cost_eur AS parts_cost_after,
       s.labor_fix_rule, b.labor_hours    AS labor_hours_before, s.labor_hours    AS labor_hours_after
FROM   <catalog>.<schema>.silver_maintenance_history s
JOIN  (SELECT DISTINCT record_hash AS _record_hash, parts_cost_eur, labor_hours
       FROM <catalog>.<schema>.bronze_maintenance_history) b
  ON   b._record_hash = s._record_hash
WHERE  s.parts_fix_rule <> 'UNCHANGED' OR s.labor_fix_rule <> 'UNCHANGED';
```

`DISTINCT` is there because the in-file duplicates share one hash and have identical values.

---

## 7. Data-quality expectations (the manifest)

| Severity | Effect |
|---|---|
| Drop | Row removed; it stays in Bronze |
| Fix | Value corrected; recorded in a `*_fix_rule` column or `equipment_resolution` |
| Flag / Warn | Boolean or count; value unchanged; run continues |
| Field quarantine | Field null; row stays; quarantine record |
| Row quarantine | Row not in Silver; quarantine record |
| Error | If above 0, the run stops before writing Silver |

| Rule | Check | Severity | Expected |
|---|---|---|---|
| SCHEMA_CONTRACT | Required Bronze and reference columns present | Error (stops before the build; not in the counts) | 0 |
| TYPE_CAST | Non-empty value that doesn't parse | Error | 0 |
| DUP_EXACT | Exact duplicate of the business columns (on 10,000 raw) | Drop | 100 |
| VERSION_LATEST | Superseded version | Drop | 100 |
| SOURCE_KEY_FORMAT | `source_system_key` format | Error | 0 |
| DOMAIN_CODES | Codes in their lists; technician format | Error | 0 |
| COMPONENT_CODE_MAP | `component` ↔ `component_code` | Error | 0 |
| WO_NULL_NON_LEGACY | Null `work_order_id` on CMMS | Error | 0 |
| WO_ORPHAN | `work_order_id` not in work orders | Warn | 0 |
| RC_NULL_IFF_NONE | Root cause null ⇔ failure mode NONE | Error | 0 |
| NDD_IFF_PLANNED | Next due date set ⇔ failure mode NONE | Error | 0 |
| EQ_MATCHES_WO | Activity and work order equipment differ | Error | 0 |
| EQ_RECOVERED_FROM_WO | Missing equipment recovered | Fix | 32 |
| EQ_MISSING_UNRECOVERABLE | Missing equipment, no work order | Row quarantine | 10 |
| EQ_UNREGISTERED | Equipment not in the master | Flag | 58 |
| FM_MATCHES_WO | Failure mode equals the work order's | Error | 0 |
| RC_CONFLICT_WITH_WO | Root cause differs from the work order's | Warn | 10 |
| ACTIVITIES_PER_WO | More than 2 activities on a work order | Warn | 0 |
| TS_END_BEFORE_START | End before start | Field quarantine | 100 |
| TS_AFTER_EXTRACT | Start or end after `ingestion_ts` | Warn | 0 |
| TS_MODIFIED_BEFORE_END | `last_modified_ts` < end | Warn | 0 |
| NDD_AFTER_END | Next due date not after the end date | Warn | 0 |
| C1 / C2 / C3 | Cost zeroed / recovered / unrecoverable | Fix / Fix / Field quarantine | 13 / 8 / 0 |
| Q1 / Q2 | Quantity zeroed / made positive | Fix | 10 / 15 |
| PARTS_QTY_COST_CONSISTENT | Quantity 0 ⇔ cost 0 | Error | 0 |
| L1 / L2 / L3 | Sentinel nulled / minutes converted / negative made positive | Fix | 22 / 100 / 32 |
| L4_RECOVERED / L4_UNRECOVERABLE | Sentinel recovered / not | Fix / Field quarantine | 22 / 0 |
| LABOR_UNEXPLAINED_RATIO | Factor not 1 or 2 after fixes | Error | 0 |

The expected counts are **test scaffolding** for this synthetic dataset: they live in `MANIFEST` in `test_mh_silver_logic.py`, not in the notebook. Real data has no fixed defect counts.

---

## 8. Reconciliation

### 8.1 Row flow (every Bronze row has exactly one outcome)

Written to `silver_dq_reconciliation` (shared, append-only), one row per source batch plus `ALL`: `run_id`, `entity`, `check_name` (`ROW_FLOW:<batch>`), `status` (`MATCH` / `MISMATCH`), `row_count` (Silver rows), `left_value` (Bronze rows), `right_value` (Silver + duplicates + superseded + row quarantine), `diff`, `run_ts`.

| | Batch 1 | Batch 2 | Total |
|---|---|---|---|
| Bronze | 9,595 | 405 | 10,000 |
| − exact duplicates | 98 | 2 | 100 |
| − superseded versions | 100 | 0 | 100 |
| = after dedup | 9,397 | 403 | 9,800 |
| − row quarantine | 10 | 0 | 10 |
| = Silver | 9,387 | 403 | 9,790 |

### 8.2 Against work orders and legacy totals: moved to Gold

Summing activity labour and parts per closed work order and comparing them with the work order (with FX tolerances), and totalling the legacy rows, are aggregation, not cleaning. They are not computed in Silver. Expected results for the Gold owner, on the 8,949 closed work orders with activities:

| Check | Expected |
|---|---|
| Labour (tolerance 0.01 h) | 8,897 `MATCH`, 0 `MISMATCH`, 52 `WO_VALUE_INVALID` (28 × 9999, 24 negative on the work order) |
| Parts (tolerance €0.01 for EUR; `max(€0.01, 0.1%)` for converted) | 8,922 `MATCH`, 0 `MISMATCH`, 24 `WO_VALUE_INVALID`, 3 `FX_UNAVAILABLE` |
| Legacy | 40 kept legacy rows: report their parts cost and labour hours separately (no work order) |

---

## 9. Silver table: `silver_maintenance_history`

Grain: one maintenance activity, latest version. Primary key `maintenance_id`. Rows today: 9,790. Delta, no partitioning (10,000 rows would only make tiny files), `delta.enableChangeDataFeed = true`, column comments from this table (`SILVER_SCHEMA` in the code).

| Column | Type | Null? | Meaning |
|---|---|---|---|
| `maintenance_id` | string | no | Activity ID `MH-nnnnn` |
| `work_order_id` | string | legacy only | Work order the activity ran under |
| `equipment_id` | string | no | Asset worked on, resolved per 5.4 (unregistered IDs kept as delivered) |
| `activity_start_ts` | timestamp | no | Work started, UTC |
| `activity_end_ts` | timestamp | yes | Work finished, UTC. Null after TS_END_BEFORE_START |
| `activity_type` | string | no | Inspect, Replace, Align, Clean, Service, Calibrate, Lubricate, Repair, Adjust |
| `component` | string | no | Component worked on |
| `component_code` | string | no | Component code |
| `failure_mode_code` | string | no | Failure addressed; `NONE` = planned work |
| `root_cause_code` | string | yes | Root cause. Null = planned work (valid) |
| `detection_method` | string | no | How the need was found |
| `action_description` | string | no | Action text |
| `parts_qty` | int | no | Spare parts used, ≥ 0 |
| `parts_cost_eur` | decimal(12,2) | after C3 only | Spare-parts cost of this activity, EUR |
| `labor_hours` | decimal(8,2) | after L4 failure only | Booked person-hours (minimum booking 0.25 h per person) |
| `technician_id` | string | no | `TECH-nn`. Pseudonymous |
| `outcome` | string | no | `Resolved` or `Temporary fix` |
| `next_due_date` | date | yes | Next planned occurrence. Null = failure work (valid) |
| `last_modified_ts` | timestamp | no | Last change in the source system, UTC |
| `record_version` | int | no | Version kept |
| `parts_fix_rule` | string | no | Parts rules applied, `;`-joined, or `UNCHANGED` |
| `labor_fix_rule` | string | no | Labour rules applied, `;`-joined, or `UNCHANGED` |
| `equipment_resolution` | string | no | `SOURCE`, `RECOVERED_FROM_WO`, `UNREGISTERED` |
| `dq_unknown_equipment` | boolean | no | Equipment not in the equipment master |
| `dq_rc_conflict_with_wo` | boolean | no | Root cause differs from the work order's |
| `source_file`, `ingestion_ts`, `batch_id`, `source_system`, `source_system_key` | | no | Source lineage |
| `_ingestion_ts`, `_batch_id`, `_source_file`, `_source_system`, `_record_hash` | | no | Bronze lineage of the kept row |
| `_silver_run_id` | string | no | Run that last wrote the row |
| `_silver_processed_ts` | timestamp | no | When the row was last written |
| `_silver_row_hash` | string | no | SHA-256 of business, fix-rule and flag columns |

**Not in Silver, on purpose:** the raw copies (`*_raw`; originals stay in Bronze), `labor_cost_eur`, `activity_cost_eur`, `work_category`, `root_cause_status`, `next_due_status`, `labor_factor`, `labor_factor_check`, `is_legacy`, `activity_duration_h`, `activity_local_date`, `dq_lifecycle_conflict`, `lifecycle_conflict_reason`. See section 12.

---

## 10. Parameters

All in the `CONFIG` dict at the top of `mh_silver_logic.py`. Catalog and schema come from widgets; the table widgets default to the names under `tables`.

```yaml
bronze_loaded_status: SUCCESS            # the Bronze notebook writes SUCCESS or RESCUED
bronze_column_map:                       # Bronze notebook column -> name used here (2.2)
  batch_id: _batch_id
  source_batch_id: batch_id
  ingestion_timestamp: _ingestion_ts
  source_filename: _source_file
  record_hash: _record_hash
  load_status: _load_status
timestamp_pattern: "yyyy-MM-dd'T'HH:mm:ss'Z'"
date_pattern: yyyy-MM-dd
legacy_source_system: LEGACY_CMMS

no_parts_activity_types: [Inspect, Align, Adjust, Calibrate]   # C1 domain assumption
labour_sentinels: [999]
min_booking_h: 0.25
allowed_factors: [1, 2]                  # L2 domain assumption: 1 or 2 people per activity
factor_tolerance: 0.05
max_activities_per_work_order: 2

codes:
  activity_type: [Inspect, Replace, Align, Clean, Service, Calibrate, Lubricate, Repair, Adjust]
  failure_mode_code: [NONE, BRG-WEAR, MISALIGN, IMBAL, OVERHEAT, LUB-FAIL, WIND-INS, SEAL-LEAK,
                      CAVIT, VALVE-FAIL, GEAR-WEAR, CLOG]
  root_cause_code: [RC-WEAR, RC-CONTAM, RC-LUB, RC-INST, RC-OPER, RC-MAINT, RC-DESIGN, RC-MATL]
  detection_method: [scheduled_pm, inspection, operator_report, condition_monitoring, alarm]
  outcome: [Resolved, Temporary fix]
technician_pattern: "^TECH-[0-9]{2}$"
component_to_code:
  Whole asset: GENERAL
  Shaft coupling: COUPLING
  Drive-end bearing: BRG-DE
  Bearing and coupling: BRG-DE
  Non-drive-end bearing: BRG-NDE
  Cooling system / heat exchanger: COOLING-SYS
  Lubrication system: LUBE-SYS
  Sensor package: SENSOR-PKG
  Oil filter: OIL-FILTER
  Rotor assembly: ROTOR
  Stator winding: STATOR-WDG
  Mechanical seal: SEAL-MECH
  Impeller: IMPELLER
  Valve assembly: VALVE
  Gear set: GEAR-SET
  Suction strainer / filter: STRAINER
  Fan blade set: FAN-BLADE

tables:
  work_orders: silver_work_orders
  equipment: silver_equipment
  silver: silver_maintenance_history
  quarantine: silver_dq_quarantine
  audit: dq_results
  reconciliation: silver_dq_reconciliation
```

The expected defect counts (section 7) are not a parameter: they are in the test file.

---

## 11. Traps (each one is a defect the data was built to catch)

1. `abs()` on a negative parts cost (use C1–C3).
2. Hard-coded offsets (+5, −10) or the literal `EQ-9999`.
3. Detecting minutes with "> 60" (use the ratio).
4. Swapping start and end, or estimating the end from labour.
5. Placeholders in `root_cause_code` or `next_due_date`.
6. Dropping the legacy rows.
7. Recovering an activity value from an invalid work-order value.
8. Converting currencies in this notebook (read the work order's `parts_cost_eur`).
9. `double` for money or hours.
10. Appending to the quarantine table instead of MERGE.
11. Changing a value without recording the rule in `parts_fix_rule`, `labor_fix_rule` or `equipment_resolution`.
12. Adding Gold logic: costs, categories, status labels, the reporting period, lifecycle checks, sums against work orders.

---

## 12. What changed against the earlier rules doc

The team agreed that Silver holds only cleaning and its evidence. Removed:

| Removed | Reason | Where it goes |
|---|---|---|
| `labor_cost_eur`, `activity_cost_eur` | Measures | Gold, `fact_maintenance_work_order` |
| `work_category`, `root_cause_status`, `next_due_status` | Status logic and labels. RC_NULL_IFF_NONE and NDD_IFF_PLANNED already validate the nulls | Gold / semantic model |
| `labor_factor` | Meaning unconfirmed, used by nothing | Dropped |
| `labor_factor_check` | Can't carry information: `UNEXPLAINED_RATIO` stops the run, `INVALID_TIMESTAMPS` repeats the null end | Dropped; LABOR_UNEXPLAINED_RATIO stays as a rule count |
| `is_legacy` | Copies `source_system` | Gold filters on `source_system` |
| `activity_duration_h`, `activity_local_date` | Working values | Computed during the run; Gold derives them if needed |
| NULL_STATUS_MISSING | Its columns are gone | Dropped |
| UNIT_COST_PLAUSIBLE, COMPONENT_FITS_TYPE | Expect 0; ranges were profiled from this same data | Backlog |
| `parts_qty_raw`, `parts_cost_eur_raw`, `labor_hours_raw`, `activity_end_ts_raw` | Copies of Bronze | Bronze, joined on `_record_hash` (query in 6.5) |
| `silver.recon_maintenance_vs_work_order` | Work-orders owner can `GROUP BY work_order_id` on Silver | Dropped |
| Root-cause resolution switch | Resolving the conflict is Gold's job | Gold |
| Lifecycle check: EQ_LIFECYCLE_CONFLICT, `dq_lifecycle_conflict`, `lifecycle_conflict_reason` | Only warned, never changed a value; needs SCD2 equipment, site time zones and defect flags | Gold (5.4) |
| Interim mode (`dependency_source`, building work orders and equipment from Bronze, FX derivation at 55 EUR/h) | A second copy of other teams' logic; FX is business logic | Dropped: references are read from Silver only (section 3) |
| Labour / parts against work orders, legacy totals (old 8.2–8.4) | Aggregation and business reconciliation | Gold (8.2) |
| Defect manifest in the notebook config, `expected_count` / `manifest_match` in the audit | Test scaffolding | `MANIFEST` in the test file |

---

## 13. Open questions, ADRs, risk log

**Open questions**

| Question | Proposal | Ask |
|---|---|---|
| Root-cause conflicts (10) | Most recently modified record wins, applied in Gold | Work-orders owner |
| Work on decommissioned assets (9, found by the Gold lifecycle check) | Show on Critical exceptions | Equipment owner: wrong date or uncancelled work? |
| Meaning of labour factor 2 | Not stored | Instructor |
| Technician differs from the work order's (1,478 activities) | Information, not a defect | Confirm several technicians per work order is normal |

**ADRs:** Silver = cleaning only; rebuild-and-MERGE refresh; quarantine pattern with resolution status; ratio-based minutes detection; recovery from valid work-order values only; legacy rows kept in Silver; nulls instead of placeholders; lifecycle check and work-order reconciliation in Gold; references read from Silver only; schema change from project instructions §4.3.

**Risk log:** the notebook can't run until `silver_work_orders` (with the 6 contract columns, invalid values as null, parts cost in EUR) and `silver_equipment` exist; Bronze deduplicating on `_record_hash` would hide the in-file duplicates; a Silver table created by an older version must be dropped once (6.1).

**Data dictionary:** fix the note on the 10 unrecoverable equipment IDs; add the Silver columns from section 9; describe `labor_hours` as booked person-hours with a 0.25 h minimum booking per person.
