# Silver rules: `maintenance_history`

How to clean `maintenance_history_2.csv` from Bronze to Silver in a Databricks notebook.

| | |
|---|---|
| Team | 3, Predictive Maintenance Analytics |
| Owner | P1 Data engineering |
| Input | `bronze_maintenance_history` only (batches `BATCH-20260915-01` and `BATCH-20260922-02`) |
| Output | **Three tables only:** `silver_maintenance_history`, `silver_dq_quarantine`, `dq_results`. Each is created on the first run; if it already exists, the run writes into it (6.1) |
| Depends on | **Nothing outside Bronze.** No other Silver table is read (section 3) |
| Code | `databricks/silver/silver-max/mh_silver_logic.py`, tests in `test_mh_silver_logic.py` |
| Based on | Profiling note `maintenance_history_bronze_to_silver.pdf` (2026-10-02) and its re-verification (2026-10-03) |
| Status | Draft, standalone-cleaning version (2026-10-06). Items marked **OPEN** need agreement (section 13). Counts marked **TBC** must be confirmed on the first run |

Table names are the code defaults. Catalog and schema come from notebook widgets (`catalog`, `schema`), never from code.

> **How to read the counts.** *Raw* means the 10,000 Bronze rows. *After dedup* means the 9,800 rows left once exact duplicates are removed and only the latest `record_version` per `maintenance_id` is kept. Every rule runs on those 9,800 rows. The counts are the **defect manifest**: the test script (`MANIFEST` in `test_mh_silver_logic.py`) asserts them. If a run gives a different number, find out whether the code or the data changed. Never edit the number to make a test pass. (Exception: the counts marked **TBC** in this version were derived on paper after the redesign; set them in `MANIFEST` from the first run, after checking that the run is right.)

---

## 1. What Silver does, and what it doesn't

**Team rule (record it in an ADR): Silver holds cleaned data and the evidence of the cleaning. Status logic, measures, labels, lifecycle checks and aggregation live in Gold.**

**This notebook uses only the maintenance rows themselves.** It reads no other Silver table. A value that is wrong or missing and can only be fixed with data from another table is **not** fixed here: it goes to quarantine. A value that isn't wrong as such but can only be judged against another table (e.g. an equipment ID that may not be registered) is kept as delivered, and Gold judges it.

A calculation belongs in Silver only if you need it to find or fix a bad value from the row itself (or its sibling rows in this table). Ask of every new column: *does it describe the cleaning (what was wrong, what we did), or the business?* Only the first kind stays here.

| Silver (this document) | Gold (not here) |
|---|---|
| Types, UTC timestamps, trimmed strings, code checks | Costs: `labor_cost_eur`, `activity_cost_eur`, maintenance cost |
| Exact duplicates and record versions | Work categories (planned PM, planned inspection, failure) |
| Fixes that need only the row: cost on no-parts activities, minutes booked as hours, negative signs | Labels such as "Not applicable (planned work)" |
| Quarantine for what this notebook can't fix: missing equipment (row), invalid end, negative parts cost, 999 labour, missing technician (field) | **Anything that needs another table:** filling null parts cost and labour from the work order, equipment registration (unregistered IDs), comparisons with the work order (equipment, failure mode, root cause), work-order orphans |
| Rule counts and the row-flow check (both in `dq_results`) | Lifecycle check (activity outside the asset's install/decommission dates) |
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

The 25 source columns as delivered, plus the Bronze audit columns. The team's Bronze notebook names them differently; `clean_maintenance_history` renames them in step 0 (parameter `bronze_column_map`):

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
| `equipment_id` | `EQ-nnnnn` | defect → **row quarantine** (5.4) |
| `technician_id` | `TECH-nn` | defect → **null + field quarantine**, row stays (5.5) |
| `root_cause_code` | code | not applicable (planned work) |

> **Check with the Bronze owner.** The 100 in-file duplicates share the same `_record_hash`. If Bronze deduplicates on the hash, they never reach Silver, DUP_EXACT counts 0 and the row flow stops matching the source file. Bronze idempotency should work per batch (skip or replace a batch already loaded), not per row hash.

---

## 3. No reference tables

This notebook reads **only `bronze_maintenance_history`**. It does not read `silver_work_orders`, `silver_equipment` or any other Silver table, so it has no run-order dependency on the other Silver notebooks and can run in parallel with them:

```
bronze → silver_maintenance_history ┐
bronze → silver_work_orders         ├→ gold
bronze → silver_equipment           ┘
```

SCHEMA_CONTRACT checks only the Bronze columns (section 2.2) and stops with a message listing every missing column.

**What used to need a reference table, and where it goes now:**

| Was done in Silver | Needed | Now |
|---|---|---|
| EQ_RECOVERED_FROM_WO: fill a missing `equipment_id` from the work order (32 rows) | `silver_work_orders` | **Row quarantine** under EQ_MISSING (5.4). Whether to recover these later is **OPEN** (section 13) |
| EQ_UNREGISTERED and `dq_unknown_equipment` (58 rows, today all `EQ-9999`) | `silver_equipment` | Gold: lookup against the equipment dimension, Unknown member. Silver keeps the ID as delivered |
| `equipment_resolution` | `silver_work_orders`, `silver_equipment` | Dropped: without recovery and lookup it would always say `SOURCE` |
| EQ_MATCHES_WO, FM_MATCHES_WO | `silver_work_orders` | Gold consistency checks |
| RC_CONFLICT_WITH_WO and `dq_rc_conflict_with_wo` (10 rows) | `silver_work_orders` | Gold (it already resolves the conflict there) |
| WO_ORPHAN | `silver_work_orders` | Gold |
| C2 `NEG_COST_RECOVERED_FROM_WO` (8 rows) | `silver_work_orders` | Silver sets the cost to **null + field quarantine** (5.7). Gold may fill it from the work order |
| L4 `SENTINEL_999_RECOVERED` (22 rows) | `silver_work_orders` | Silver sets the hours to **null + field quarantine** (5.8). Gold may fill them from the work order |

**For the work-orders owner:** nothing changes on your side. Your Bronze lookup of missing work-order `equipment_id` reads `bronze_maintenance_history`, not this table, so it still works. This notebook no longer reads your 6 contract columns, so the "null when invalid" handover isn't needed for it any more (Gold still needs it, section 8.2).

---

## 4. Processing order

Each step relies on the one before it. The code in `mh_silver_logic.py` has **two pipeline functions and a `main()` without inputs**:

```python
def clean_maintenance_history(bronze_df: DataFrame, config: dict = CONFIG) -> DataFrame:
    """Steps 0-7: the cleaning. Reads nothing and writes nothing.
    Returns the deduplicated, cleaned rows with every _f_<RULE> column."""

def create_silver_tables(cleaned_df: DataFrame, catalog: str, schema: str,
                         run_id: str, config: dict = CONFIG) -> None:
    """Steps 8-9: builds Silver, quarantine, rule counts and row flow,
    runs the error gate, then writes the three output tables
    (creating any that don't exist yet)."""

def main() -> None:
    """No inputs. Reads the catalog and schema widgets, creates the run_id,
    reads Bronze, then calls the two functions above."""

main()
```

| Step | What | Function | Rules | Section |
|---|---|---|---|---|
| 0 | Keep `SUCCESS` rows, rename the Bronze columns, check them | `clean_maintenance_history` | SCHEMA_CONTRACT | 2.2 |
| 1 | Standardise: trim, empty → null, cast | `clean_maintenance_history` | TYPE_CAST | 5.1 |
| 2 | Flag exact duplicates (before any sum, so nothing counts twice) | `clean_maintenance_history` | DUP_EXACT | 5.2 |
| 3 | Flag all but the latest version per `maintenance_id` | `clean_maintenance_history` | VERSION_LATEST | 5.2 |
| 4 | Check the keys: work order, equipment, technician | `clean_maintenance_history` | WO_NULL_NON_LEGACY, EQ_MISSING, TECH_ID_MISSING | 5.3–5.5 |
| 5 | Fix timestamps (step 6 needs a valid duration) | `clean_maintenance_history` | TS_*, NDD_AFTER_END | 5.6 |
| 6 | Fix parts cost, then parts quantity; fix labour hours | `clean_maintenance_history` | C1–C2, Q1–Q2, L1–L3 | 5.7, 5.8 |
| 7 | Check internal consistency and the code lists | `clean_maintenance_history` | section 7 | 5.9, 5.10 |
| 8 | Build Silver, quarantine, rule counts, row flow; collect error counts | `create_silver_tables` | | 6, 8 |
| 9 | Stop if an error rule fired; otherwise create any missing output table, MERGE Silver, MERGE quarantine, append rule counts and row flow to `dq_results` | `create_silver_tables` | | 6 |

How the work is split:

- **`main()` takes no parameters.** Everything it needs comes from inside the notebook: `catalog` and `schema` from the widgets, the table names and rule settings from `CONFIG`, a new `run_id` per run. It only reads `bronze_maintenance_history` with `spark.table`, calls `clean_maintenance_history`, then passes the result to `create_silver_tables`. No rule logic lives in `main()`.
- **`clean_maintenance_history` is pure cleaning.** It takes the Bronze DataFrame and returns a DataFrame; it reads no table and writes nothing. That is what the tests call with small hand-made DataFrames. Small private helpers (names starting with `_`, e.g. `_standardise`, `_fix_labour`) can hold the step logic, but only this function calls them.
- **`create_silver_tables` is the only place that writes.** It turns the cleaned rows into the Silver rows, the quarantine records, the rule counts and the row flow, runs the error gate (6.2) before any write, then writes the three output tables: `CREATE TABLE IF NOT EXISTS` for each, MERGE into `silver_maintenance_history`, MERGE into `silver_dq_quarantine`, append to `dq_results`. An existing table is written into, never dropped (6.1). Row and field quarantine (6.3) are decided here from the `_f_<RULE>` columns.

Every rule sets a boolean working column `_f_<RULE>` inside `clean_maintenance_history`. Counting, the error gate and the DQ results in `create_silver_tables` read those columns, so **adding a rule** means one line in `RULES` (severity, dimension, description) and one `_f_<RULE>` column in the cleaning function.

---

## 5. Rules

### 5.1 Standardise

- Set the Spark session time zone to UTC. Parse timestamps with the pattern `yyyy-MM-dd'T'HH:mm:ss'Z'`; parse `next_due_date` as a date.
- **Whitespace.** Step 1 trims leading and trailing spaces on every source column that arrives as a string, and turns a value that is empty after trimming into null. Columns Bronze already typed (numbers, timestamps) carry no whitespace.
  - It runs **before** parsing, deduplication and every rule. So `" 12.50"` still parses, two rows that differ only in padding count as DUP_EXACT, and a whitespace-only `equipment_id` or `technician_id` counts as missing.
  - The delivered, untrimmed row is captured first (`_w_source_json`), so quarantine `original_values` show the value as received.
  - Spark `trim` removes the space character only. Tabs, line breaks and non-breaking spaces stay: in a code column they fail DOMAIN_CODES (error, the run stops), and in `action_description` they pass.
  - Inner spaces are not collapsed: `"Temporary  fix"` fails DOMAIN_CODES instead of being fixed silently.
  - Trimming is standardisation, not a counted rule: it writes no `*_fix_rule` and no rule count. If the source starts sending padded values, add a count-only FIX rule (e.g. `WHITESPACE_TRIMMED`).
  - Not trimmed here: the Bronze audit columns (system-written).
  - A scan of `maintenance_history_2.csv` (2026-10-06) found no leading or trailing whitespace, no whitespace-only values and no double inner spaces. The trim is defensive.
- Don't change case: codes already arrive in canonical case, and DOMAIN_CODES fails the run if that changes.
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
| `equipment_id` | 42 | Defect: row quarantine (5.4) |
| `technician_id` | 0 | Defect if it appears: null + field quarantine (5.5) |
| `root_cause_code` | 5,337 | Valid: planned work has no failure |
| `next_due_date` | 4,463 | Valid: failure work has no next planned date |

- **Keep the valid nulls. Never fill them with placeholders** such as `INSP`, `MAIN` or `"NO"`. A placeholder mixes two meanings in one column, would outnumber every real root cause in charts, and in `next_due_date` would turn a date column into text.
- Validate them instead: **RC_NULL_IFF_NONE** (root cause is null exactly when `failure_mode_code = NONE`) and **NDD_IFF_PLANNED** (next due date is set exactly when `failure_mode_code = NONE`). Both are error rules with 0 violations.
- `detection_method = inspection` covers both planned inspections and inspections after a breakdown. Never use it to decide whether work was planned; use `failure_mode_code`.
- **WO_NULL_NON_LEGACY** (error, 0): a null `work_order_id` is valid only when `source_system = LEGACY_CMMS`.
- Whether a `work_order_id` exists in the work-orders table (the old WO_ORPHAN) is checked in Gold: it needs another table.
- **Legacy rows stay in Silver** (when they have an equipment ID). Choosing the reporting period is a Gold decision. Don't filter them out at ingestion or here; source rows would vanish without explanation.

### 5.4 Equipment key

**EQ_MISSING (row quarantine, 42 rows: 32 CMMS, 10 LEGACY_CMMS)**

- `equipment_id` is null after standardising → the row goes to quarantine only, not to Silver. The quarantine record holds the whole source row in `original_values`.
- **Don't recover it.** Profiling shows the 32 CMMS rows have a work order that carries the ID, but reading the work order means reading another table, which this notebook doesn't do. Whether those 32 activities should come back through a later step is **OPEN** (section 13).
- The 10 LEGACY_CMMS rows have no work order at all. (The data dictionary says they belong to work orders that are also missing the ID. That is wrong: fix the dictionary.)

**Unregistered equipment (58 CMMS rows, today all `EQ-9999`)**

- Not checked here: deciding whether an ID is registered needs `silver_equipment`. Silver **keeps the source value as delivered**: don't null it, don't quarantine the row, and never hard-code `EQ-9999` anywhere.
- Gold looks the ID up in the equipment dimension and maps a miss to the Unknown member. "Unregistered" and "missing" stay different problems for the master-data team: missing is in quarantine, unregistered is in Gold.

**Lifecycle check: in Gold.** Whether an activity falls between the asset's `install_date` and `decommission_date` needs the full equipment table. Expected findings for the Gold owner: 53 conflicts (44 on assets with an install-date defect, 9 planned activities 1–20 days after decommission; **OPEN**, section 13).

### 5.5 Technician ID (TECH_ID_MISSING, field quarantine, 0 expected)

- `technician_id` is null after standardising, **and that is the row's only blocking problem** → the row stays in Silver with `technician_id` null, and a field-quarantine record is written (`field_name = technician_id`, `original_values` = the value as received, e.g. `{"technician_id": ""}`).
- If the row also has no `equipment_id`, EQ_MISSING wins: the whole row goes to quarantine with one ROW record (section 6.3).
- **Don't fill a placeholder** (`TECH-00`, `UNKNOWN`) and don't take the technician from the work order. Gold maps the null to its Unknown member.
- A non-null value that doesn't match `TECH-nn` is a format change, not a missing value: it still fails DOMAIN_CODES (error, the run stops).
- Profiling found no null `technician_id`, so the rule expects 0. It is there so a future batch with gaps doesn't fail the run or lose activities.

### 5.6 Activity timestamps (TS_END_BEFORE_START, field quarantine, 100)

100 rows have `activity_end_ts` before `activity_start_ts`. Profiling against the work orders (2026-10-02) showed which value is wrong; the notebook doesn't read the work orders, the rule is fixed from that evidence:

- **The start is right.** In 93 of the 100 rows it equals the work order's `actual_start_ts` to the second; the other 7 are the second activity on a two-activity work order.
- **The end is wrong.** In 98 of the 100 it is even earlier than when the work order says work began.

Rule: keep the start, set `activity_end_ts` to null (the original stays in Bronze and in the quarantine record's `original_values`), write a field-quarantine record. The row stays in Silver: its labour, cost and failure data are valid.

- **Don't swap start and end.** That moves the verified start into the end column and makes the bad value the new start.
- **Don't estimate the end** as start + labour hours: you don't know whether one or two people worked.

Extra checks, all warn, 0 today: **TS_AFTER_EXTRACT** (start or end after the row's `ingestion_ts`), **TS_MODIFIED_BEFORE_END** (`last_modified_ts` < `activity_end_ts`, valid ends only), **NDD_AFTER_END** (`next_due_date` not after the activity's end date; start date when the end is null).

### 5.7 Parts cost and quantity

Why `abs()` is wrong for cost:

- 13 rows are −5.00 on Inspect, Calibrate or Adjust activities. These activity types never use parts, so the true value is 0. `abs()` would invent €5 of parts on an inspection.
- 8 rows are on parts activities and the size is wrong as well as the sign. Example: MH-00315 shows −175.13, but its work order has `parts_cost` 170.13 and no other activity. `abs()` overstates all 8. The right value can only come from the work order, so Silver can't fix these: it nulls them and quarantines the field.

> **Domain assumption (C1).** Inspect, Align, Adjust and Calibrate activities never use parts (parameter `no_parts_activity_types`). C1 changes values based on this business knowledge; if the assumption changes, change the parameter, not the code.

**Cost rules, in order** (recorded in `parts_fix_rule`):

| Rule | `parts_fix_rule` | Logic | Rows |
|---|---|---|---|
| C1 | `NO_PARTS_ACTIVITY_COST_ZEROED` | `activity_type` in `no_parts_activity_types` and cost ≠ 0 → 0 | 13 |
| C2 | `NEG_COST_NULLED` | Cost < 0 on any other activity → null and a field-quarantine record. **Never `abs()`** | 8 |

Gold may fill a C2 null from the work order: work order `parts_cost_eur` − sum of the other activities' cost, only when exactly one activity on the work order is null, the work-order value is valid and the result is ≥ 0. (MH-03785 sits on a GBP work order; that is why Gold must use the work order's EUR value.)

Never write "add 5" or "subtract 10" logic, even though the data shows fixed offsets: that is how the defects were generated, not a business rule.

**Quantity rules, after the cost rules:**

| Rule | `parts_fix_rule` | Logic | Rows |
|---|---|---|---|
| Q1 | `ZERO_COST_QTY_ZEROED` | Cleaned cost = 0 and quantity ≠ 0 → 0 | 10 **TBC** |
| Q2 | `NEG_QTY_CORRECTED` | Quantity < 0 and cleaned cost ≠ 0 or null → `abs()`. An inferred fix: no second source holds a quantity | 15 **TBC** |

The Q1 and Q2 counts come from the earlier version, where C2 recovered the 8 costs instead of nulling them. They only change if one of those 8 rows also has a quantity defect; confirm on the first run.

`parts_fix_rule` lists every applied rule joined with `;` (e.g. `NO_PARTS_ACTIVITY_COST_ZEROED;ZERO_COST_QTY_ZEROED`), or `UNCHANGED`.

After the fixes: **PARTS_QTY_COST_CONSISTENT** (error, 0): quantity is 0 exactly when cost is 0 (rows with null cost are skipped).

### 5.8 Labour hours

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
| L1 | `SENTINEL_999_NULLED` | Value in `labour_sentinels` (999) → null and a field-quarantine record | 22 |
| L2 | `MINUTES_TO_HOURS` | `ratio ÷ 60` is a valid factor (1 or 2, ±0.05) → divide by 60 | 100 |
| L3 | `NEGATIVE_ABS` | Negative → `abs()`. Profiling showed all 32 then match their work order, which verifies the fix | 32 |
| | `UNCHANGED` | Everything else, including factor-2 rows and 0.25 h / 0.5 h minimum bookings | 9,646 |

Gold may fill an L1 null from the work order: work order `labor_hours` − sum of its other activities' fixed hours, only when exactly one null is on the work order, the work-order value is valid and the result is > 0.

**Detect minutes by ratio, never with a "> 60" threshold.** 5 genuine values exceed 60 h (e.g. MH-00160 = 64.31 h on a long activity) and 10 minute values are below 60. The bands are clear: 85 rows have `ratio ÷ 60` between 0.993 and 1.005, 15 between 1.998 and 2.001, and no other row is above 0.034.

The 100 rows with an invalid end have no duration, so L2 can't run on them. They keep their labour (profiling: all of them match their work order once siblings are fixed).

**LABOR_UNEXPLAINED_RATIO** (error, 0): after L1–L3, every row with a duration and labour hours has a valid factor. A failure means a new kind of defect nobody has handled, so the run stops.

### 5.9 Internal consistency

- **ACTIVITIES_PER_WO** (warn, 0): more than `max_activities_per_work_order` (2) activities on one `work_order_id` within this table. A business expectation rather than a data defect, so it only warns.
- Consistency with the work order itself (equipment, failure mode, root cause) needs `silver_work_orders`, so it is checked in Gold (section 3).

### 5.10 Code checks

- **DOMAIN_CODES** (error, 0): `activity_type`, `component_code`, `failure_mode_code`, `root_cause_code` (when set), `detection_method`, `outcome` are in their code lists (section 10); `technician_id` matches `TECH-nn` **when set** (a null is TECH_ID_MISSING, 5.5).
- **COMPONENT_CODE_MAP** (error, 0): `component` maps to `component_code` as in section 10 (`Bearing and coupling` → `BRG-DE`).
- **SOURCE_KEY_FORMAT** (error, 0): `source_system_key = source_system || '|' || maintenance_id`.

---

## 6. Writing the outputs

### 6.1 Refresh pattern: rebuild from all Bronze, MERGE into Silver

Each run rebuilds the cleaned set from **all** Bronze rows of this entity, then MERGEs it into Silver. Processing only the new batch would be wrong: corrections arrive months later and ACTIVITIES_PER_WO counts sibling activities that can arrive in different batches. At 10,000 rows a rebuild takes seconds. (Record this in an ADR.)

MERGE on `maintenance_id`:

| Condition | Action |
|---|---|
| Matched, incoming `record_version` > target | Update all columns |
| Matched, same version, `_silver_row_hash` differs | Update all columns (a rule outcome changed, e.g. after a parameter change) |
| Matched, otherwise | Nothing |
| Not matched (new row) | Insert |
| In Silver but not in the rebuilt set (now row-quarantined) | Delete |

`_silver_row_hash` = SHA-256 over the business, fix-rule and flag columns, with nulls replaced by a fixed marker before hashing. It excludes lineage and run metadata. So **a rerun on unchanged data inserts, updates and deletes 0 rows**: that is your idempotency evidence (the run prints the three numbers).

**The three output tables, new or existing.** The notebook writes to exactly three tables: `silver_maintenance_history`, `silver_dq_quarantine` and `dq_results`. For each one, `create_silver_tables` runs `CREATE TABLE IF NOT EXISTS` (columns and comments from sections 9, 6.3 and 6.4). If the table already exists, the run **writes into it**; it never drops or recreates a table, so earlier data stays.

- **Missing column.** The existing table lacks a column this version writes → the run adds it (`ALTER TABLE … ADD COLUMNS`) before writing.
- **Old column.** The existing table has a column this version no longer writes (for `silver_maintenance_history` from the previous version: `equipment_resolution`, `dq_unknown_equipment`, `dq_rc_conflict_with_wo`) → the column is kept, set to null on every row in the same run so no stale values remain, and the run prints a warning naming it. All writes use explicit column lists, so an old column never blocks a write. Drop it by hand when convenient.
- **Type clash.** An existing column has a different type (e.g. `labor_hours` as `double`) → the run stops before writing and names the column, because changing a type rewrites data and needs a person to decide.
- **Shared tables.** In `silver_dq_quarantine` and `dq_results` the run only touches rows where `entity` / `table_name` is this entity; other teams' rows are never changed.

### 6.2 Error gate

Error rules are invariants. Count them **before** any write. If any is above 0: append the DQ results, then stop the notebook with an error naming each rule and its count. Silver and quarantine are not touched.

### 6.3 Quarantine pattern (one pattern for the whole team, ADR)

- **Row quarantine:** the row is unusable (no equipment key). It goes to quarantine only.
- **Field quarantine:** one field is wrong or missing and this notebook can't fix it. The row stays in Silver with that field null, and a quarantine record is written as well.
- **Precedence:** a row-quarantined row gets only its ROW record. Field rules still fire and count on it (rule counts run on all 9,800 rows), but they write no FIELD record, because the row isn't in Silver and the ROW record already holds the whole source row.
- Fixes and warnings write **no** quarantine records. Their evidence is the `*_fix_rule` columns, the rule counts and the original values in Bronze.

| Rule | Type | Field set to null | Rows flagged |
|---|---|---|---|
| EQ_MISSING | ROW | (whole row) | 42 |
| TECH_ID_MISSING | FIELD | `technician_id` (already null) | 0 |
| TS_END_BEFORE_START | FIELD | `activity_end_ts` | 100 |
| C2 `NEG_COST_NULLED` | FIELD | `parts_cost_eur` | 8 |
| L1 `SENTINEL_999_NULLED` | FIELD | `labor_hours` | 22 |

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

Created on the first run if it doesn't exist; otherwise the run writes into the existing table (6.1). MERGE on (`entity`, `record_id`, `rule_id`), **never append**, so reruns create no duplicates. A record that no longer fires becomes `RESOLVED` (only rows of this entity).

Expected open records: **42 ROW + at most 130 FIELD (172)**. The exact FIELD number is **TBC**: the 32 CMMS rows that are now row-quarantined may also carry a timestamp, cost or labour defect, and under the precedence rule those get no FIELD record. Set the number in `MANIFEST` from the first run.

On the first run of this version, open records under the old rule IDs (`EQ_MISSING_UNRECOVERABLE`, and `C3` / `L4_UNRECOVERABLE` if any) stop firing and turn `RESOLVED` through the normal MERGE. No manual clean-up is needed.

### 6.4 DQ results

The notebook shows the rule counts per rule and source batch, plus a total with `batch_id = 'ALL'` (raw rules counted on the 10,000 Bronze rows, the rest on the 9,800 deduplicated rows).

The totals are appended to the team's shared `dq_results` table (append-only; created on the first run if it doesn't exist, otherwise written into, 6.1), one row per run and rule: `table_name` (`silver_maintenance_history`), `rule_name`, `dimension`, `severity`, `description`, `records_evaluated`, `records_failed`, `failure_rate`, `run_ts`, `run_id`. Counts go into this table, not only into notebook output. Severity, dimension and description come from `RULES` in the code. The row-flow check (8.1) goes into the same table; there is no separate reconciliation table.

### 6.5 Seeing the original values

Silver doesn't copy the source values. To show before and after for any fixed row (good demo evidence), join back to Bronze on the row hash (`main()` runs this at the end, after `create_silver_tables`):

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
| Fix | Value corrected; recorded in a `*_fix_rule` column |
| Warn | Count only; value unchanged; run continues |
| Field quarantine | Field null; row stays; quarantine record |
| Row quarantine | Row not in Silver; quarantine record |
| Error | If above 0, the run stops before writing Silver |

| Rule | Check | Severity | Expected |
|---|---|---|---|
| SCHEMA_CONTRACT | Required Bronze columns present | Error (stops before the build; not in the counts) | 0 |
| TYPE_CAST | Non-empty value that doesn't parse | Error | 0 |
| DUP_EXACT | Exact duplicate of the business columns (on 10,000 raw) | Drop | 100 |
| VERSION_LATEST | Superseded version | Drop | 100 |
| SOURCE_KEY_FORMAT | `source_system_key` format | Error | 0 |
| DOMAIN_CODES | Codes in their lists; technician format when set | Error | 0 |
| COMPONENT_CODE_MAP | `component` ↔ `component_code` | Error | 0 |
| WO_NULL_NON_LEGACY | Null `work_order_id` on CMMS | Error | 0 |
| RC_NULL_IFF_NONE | Root cause null ⇔ failure mode NONE | Error | 0 |
| NDD_IFF_PLANNED | Next due date set ⇔ failure mode NONE | Error | 0 |
| EQ_MISSING | `equipment_id` null | Row quarantine | 42 |
| TECH_ID_MISSING | `technician_id` null | Field quarantine | 0 |
| ACTIVITIES_PER_WO | More than 2 activities on a work order | Warn | 0 |
| TS_END_BEFORE_START | End before start | Field quarantine | 100 |
| TS_AFTER_EXTRACT | Start or end after `ingestion_ts` | Warn | 0 |
| TS_MODIFIED_BEFORE_END | `last_modified_ts` < end | Warn | 0 |
| NDD_AFTER_END | Next due date not after the end date | Warn | 0 |
| C1 / C2 | Cost zeroed / negative cost nulled | Fix / Field quarantine | 13 / 8 |
| Q1 / Q2 | Quantity zeroed / made positive | Fix | 10 / 15 (**TBC**) |
| PARTS_QTY_COST_CONSISTENT | Quantity 0 ⇔ cost 0 | Error | 0 |
| L1 / L2 / L3 | Sentinel nulled / minutes converted / negative made positive | Field quarantine / Fix / Fix | 22 / 100 / 32 |
| LABOR_UNEXPLAINED_RATIO | Factor not 1 or 2 after fixes | Error | 0 |

Removed from this notebook (section 3): EQ_RECOVERED_FROM_WO, EQ_UNREGISTERED, EQ_MATCHES_WO, FM_MATCHES_WO, RC_CONFLICT_WITH_WO, WO_ORPHAN, C2 recovery, L4.

The expected counts are **test scaffolding** for this synthetic dataset: they live in `MANIFEST` in `test_mh_silver_logic.py`, not in the notebook. Real data has no fixed defect counts.

---

## 8. Reconciliation

### 8.1 Row flow (every Bronze row has exactly one outcome)

Written to `dq_results` with the rule counts (there is no separate reconciliation table), one row per source batch plus `ALL`: `rule_name = ROW_FLOW:<batch>`, `severity = Warn`, `records_evaluated` = Bronze rows, `records_failed` = |Bronze − (Silver + duplicates + superseded + row quarantine)|. So `records_failed = 0` means every Bronze row has exactly one outcome. The notebook also prints the table below with `MATCH` / `MISMATCH` per batch.

| | Batch 1 | Batch 2 | Total |
|---|---|---|---|
| Bronze | 9,595 | 405 | 10,000 |
| − exact duplicates | 98 | 2 | 100 |
| − superseded versions | 100 | 0 | 100 |
| = after dedup | 9,397 | 403 | 9,800 |
| − row quarantine (EQ_MISSING) | 10 legacy + *n₁* CMMS | *n₂* CMMS | 42 |
| = Silver | 9,387 − *n₁* | 403 − *n₂* | 9,758 |

*n₁* + *n₂* = 32. The batch split of the 32 CMMS rows is **TBC**: take it from the first run.

### 8.2 Against work orders and legacy totals: in Gold

Summing activity labour and parts per closed work order and comparing them with the work order (with FX tolerances), and totalling the legacy rows, are aggregation, not cleaning. They are not computed in Silver.

**New for the Gold owner in this version:** Silver now hands over 8 null parts costs (C2) and 22 null labour hours (L1) that the previous version recovered from the work order. Fill them in Gold first (logic in 5.7 and 5.8), or the reconciliation shows them as gaps. The 32 CMMS activities in row quarantine (EQ_MISSING) are not in Silver at all, so their work orders will also show missing labour and parts until the open question in section 13 is settled.

Expected results from the previous version, on the 8,949 closed work orders with activities. **Re-run them** once Gold fills the nulls and the EQ_MISSING decision is made:

| Check | Expected (previous version) |
|---|---|
| Labour (tolerance 0.01 h) | 8,897 `MATCH`, 0 `MISMATCH`, 52 `WO_VALUE_INVALID` (28 × 9999, 24 negative on the work order) |
| Parts (tolerance €0.01 for EUR; `max(€0.01, 0.1%)` for converted) | 8,922 `MATCH`, 0 `MISMATCH`, 24 `WO_VALUE_INVALID`, 3 `FX_UNAVAILABLE` |
| Legacy | 40 kept legacy rows: report their parts cost and labour hours separately (no work order) |

---

## 9. Silver table: `silver_maintenance_history`

Grain: one maintenance activity, latest version. Primary key `maintenance_id`. Rows today: 9,758. Delta, no partitioning (10,000 rows would only make tiny files), `delta.enableChangeDataFeed = true`, column comments from this table (`SILVER_SCHEMA` in the code).

| Column | Type | Null? | Meaning |
|---|---|---|---|
| `maintenance_id` | string | no | Activity ID `MH-nnnnn` |
| `work_order_id` | string | legacy only | Work order the activity ran under |
| `equipment_id` | string | no | Asset worked on, as delivered (rows without one are in quarantine). Registration is checked in Gold |
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
| `parts_cost_eur` | decimal(12,2) | after C2 only | Spare-parts cost of this activity, EUR |
| `labor_hours` | decimal(8,2) | after L1 only | Booked person-hours (minimum booking 0.25 h per person) |
| `technician_id` | string | after TECH_ID_MISSING only | `TECH-nn`. Pseudonymous |
| `outcome` | string | no | `Resolved` or `Temporary fix` |
| `next_due_date` | date | yes | Next planned occurrence. Null = failure work (valid) |
| `last_modified_ts` | timestamp | no | Last change in the source system, UTC |
| `record_version` | int | no | Version kept |
| `parts_fix_rule` | string | no | Parts rules applied, `;`-joined, or `UNCHANGED` |
| `labor_fix_rule` | string | no | Labour rules applied, `;`-joined, or `UNCHANGED` |
| `source_file`, `ingestion_ts`, `batch_id`, `source_system`, `source_system_key` | | no | Source lineage |
| `_ingestion_ts`, `_batch_id`, `_source_file`, `_source_system`, `_record_hash` | | no | Bronze lineage of the kept row |
| `_silver_run_id` | string | no | Run that last wrote the row |
| `_silver_processed_ts` | timestamp | no | When the row was last written |
| `_silver_row_hash` | string | no | SHA-256 of business and fix-rule columns |

**Not in Silver, on purpose:** `equipment_resolution`, `dq_unknown_equipment`, `dq_rc_conflict_with_wo` (need other tables, section 3), the raw copies (`*_raw`; originals stay in Bronze), `labor_cost_eur`, `activity_cost_eur`, `work_category`, `root_cause_status`, `next_due_status`, `labor_factor`, `labor_factor_check`, `is_legacy`, `activity_duration_h`, `activity_local_date`, `dq_lifecycle_conflict`, `lifecycle_conflict_reason`. See section 12.

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

tables:                                  # the three outputs; no work_orders or equipment: nothing else is read
  silver: silver_maintenance_history
  quarantine: silver_dq_quarantine
  audit: dq_results
```

The expected defect counts (section 7) are not a parameter: they are in the test file.

---

## 11. Traps (each one is a defect the data was built to catch)

1. `abs()` on a negative parts cost (C1 zeroes, C2 nulls).
2. Hard-coded offsets (+5, −10) or the literal `EQ-9999`.
3. Detecting minutes with "> 60" (use the ratio).
4. Swapping start and end, or estimating the end from labour.
5. Placeholders in `root_cause_code`, `next_due_date` or `technician_id`.
6. Dropping the legacy rows that have an equipment ID.
7. Reading `silver_work_orders`, `silver_equipment` or any other table to fill or check a value. If a fix needs another table, quarantine the value (or keep it as delivered) and leave the rest to Gold.
8. Quarantining the whole row when only `technician_id` is missing (it is a field quarantine; the row stays).
9. Nulling or quarantining an unregistered equipment ID: it isn't missing.
10. Converting currencies in this notebook.
11. `double` for money or hours.
12. Appending to the quarantine table instead of MERGE.
13. Changing a value without recording the rule in `parts_fix_rule` or `labor_fix_rule`.
14. Adding Gold logic: costs, categories, status labels, the reporting period, lifecycle checks, sums against work orders.

---

## 12. What changed against the earlier rules docs

### 12.1 This version (2026-10-06): standalone cleaning, no Silver references

The notebook now does cleaning only and reads Bronze only. Anything it can't fix from the row itself goes to quarantine.

| Change | Before | Now |
|---|---|---|
| References | Read `silver_work_orders` (6 columns) and `silver_equipment` (ID list) | None. Bronze only; runs in parallel with the other Silver notebooks |
| Missing `equipment_id` (42) | 32 recovered from the work order, 10 row-quarantined | All 42 row-quarantined under EQ_MISSING (was EQ_MISSING_UNRECOVERABLE) |
| Missing `technician_id` | Not handled (column was not-null) | New TECH_ID_MISSING: null kept, field quarantine, row stays |
| Negative parts cost on a parts activity (8) | C2 recovered from the work order | C2 `NEG_COST_NULLED`: null + field quarantine. Old C3 removed |
| 999 labour (22) | L1 nulled, L4 recovered from the work order | L1 `SENTINEL_999_NULLED`: null + field quarantine. L4 removed |
| Unregistered equipment (58) | EQ_UNREGISTERED flag, `dq_unknown_equipment` | Kept as delivered; checked in Gold |
| Checks against the work order | EQ_MATCHES_WO, FM_MATCHES_WO, RC_CONFLICT_WITH_WO, WO_ORPHAN | Gold |
| Columns | `equipment_resolution`, `dq_unknown_equipment`, `dq_rc_conflict_with_wo` | Removed. An existing table is kept; the old columns are set to null and can be dropped by hand (6.1) |
| Outputs | Silver, quarantine, `dq_results`, `silver_dq_reconciliation` | Three tables: Silver, quarantine, `dq_results` (row flow now in `dq_results`). Created if missing, written into if they exist; never dropped |
| Silver rows | 9,790 | 9,758 |
| Open quarantine records | 110 | 42 ROW + up to 130 FIELD (**TBC**) |

The code (`mh_silver_logic.py`) and `MANIFEST` in `test_mh_silver_logic.py` must be updated to match.

### 12.2 Earlier version: Silver = cleaning and its evidence

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
| `silver.recon_maintenance_vs_work_order` | Gold can `GROUP BY work_order_id` on Silver | Dropped |
| Root-cause resolution switch | Resolving the conflict is Gold's job | Gold |
| Lifecycle check: EQ_LIFECYCLE_CONFLICT, `dq_lifecycle_conflict`, `lifecycle_conflict_reason` | Only warned, never changed a value; needs SCD2 equipment, site time zones and defect flags | Gold (5.4) |
| Interim mode (`dependency_source`, building work orders and equipment from Bronze, FX derivation at 55 EUR/h) | A second copy of other teams' logic; FX is business logic | Dropped |
| Labour / parts against work orders, legacy totals (old 8.2–8.4) | Aggregation and business reconciliation | Gold (8.2) |
| Defect manifest in the notebook config, `expected_count` / `manifest_match` in the audit | Test scaffolding | `MANIFEST` in the test file |

---

## 13. Open questions, ADRs, risk log

**Open questions**

| Question | Proposal | Ask |
|---|---|---|
| 32 CMMS activities row-quarantined for a missing `equipment_id` although their work order carries one | Accept the loss in Silver, or let a Gold step recover them from quarantine and the work order | Team / Gold owner |
| Root-cause conflicts (10) | Most recently modified record wins, applied in Gold | Work-orders owner |
| Work on decommissioned assets (9, found by the Gold lifecycle check) | Show on Critical exceptions | Equipment owner: wrong date or uncancelled work? |
| Meaning of labour factor 2 | Not stored | Instructor |
| Technician differs from the work order's (1,478 activities) | Information, not a defect; checked in Gold if needed | Confirm several technicians per work order is normal |

**ADRs:** Silver = cleaning only; **Silver reads Bronze only, no other Silver table**; quarantine for anything Silver can't fix from the row itself (row quarantine for a missing equipment key, field quarantine with null for missing technician, invalid end, negative parts cost, 999 labour); three output tables only (Silver, quarantine, `dq_results`), created if missing and written into if they exist; rebuild-and-MERGE refresh; quarantine pattern with resolution status and row-over-field precedence; ratio-based minutes detection; recovery from work orders only in Gold; legacy rows kept in Silver; nulls instead of placeholders; lifecycle check, equipment registration and work-order reconciliation in Gold; schema change from project instructions §4.3.

**Risk log:** 32 recoverable activities are out of Silver until the open question is settled, so Gold labour and parts totals for their work orders will look short; Gold must fill the 8 parts-cost and 22 labour nulls before reconciling; Bronze deduplicating on `_record_hash` would hide the in-file duplicates; a Silver table created by an older version keeps the removed columns (null) until someone drops them (6.1).

**Data dictionary:** fix the note on the 10 legacy rows without an equipment ID; add the Silver columns from section 9; describe `labor_hours` as booked person-hours with a 0.25 h minimum booking per person; mark `technician_id` as nullable (TECH_ID_MISSING).
