# Synthetic data generator

**Acceptance condition:** running the generator with a fixed seed reproduces `src/data/raw/` exactly,
yields ≥ 10,000 transactional/event records, keeps values internally consistent (keys resolve,
timelines are plausible) and injects each defect type at a documented, countable rate so detection
can be verified in `docs/quality_evidence.md`.

## Usage

Standard library only, Python 3.14+. Run from any directory:

```bash
python src/data/generate/generate_data.py                       # defaults, reproducible
python src/data/generate/generate_data.py --no-seed             # new random data each run
python src/data/generate/generate_data.py --rows-per-file 20000 --defect-rate 0.03
python src/data/generate/generate_data.py --out ./elsewhere     # different data folder
python src/data/generate/generate_data.py --upload              # generate, then upload the run to Databricks
python src/data/generate/generate_data.py --upload --folder test_datasets
python src/data/generate/generate_data.py --continue            # new activity for the delivered plant
python src/data/generate/generate_data.py --continue --days 35 --upload --folder test_datasets
```

**Without `--continue` a run builds a brand-new plant** (ids restart at `EQ-10001`, `WO-26001`, ...). Two such runs
must never share a Bronze/Silver: they reuse keys for different assets and overlap each other's downtime. Use
`--continue` for every run after the baseline (see [Continuing the delivered plant](#continuing-the-delivered-plant)).

### Example: generate new data and upload it to `test_datasets`

```bash
python src/data/generate/generate_data.py --continue --rows-per-file 1000 --days 21 --upload --folder test_datasets
```

This continues the plant delivered so far (`src/data/raw/chain.json`: the `*_2` baseline plus any earlier increments):

1. generates the next 21 days of work orders, maintenance history and sensor events, 1,000 rows per file, with ids
   continuing after the delivered ones (no equipment file: the plant is unchanged);
2. checks the result against the delivered data (ids, asset references, no downtime overlap) and records the run in
   `chain.json`, so the next run starts after it;
3. uploads the three new files to `/Volumes/data2026acc123456/team3/test_datasets/<dataset>/` (the upload is refused if the
   volume holds a run that `chain.json` does not know).

Running the same command again adds the *next* 21-day window after this one. Do not run it twice by accident, and do not
skip an increment: upload them in order. If the upload fails after generating, the files and the `chain.json` entry are
already saved, so retry only the upload: `python src/data/upload/upload_to_databricks.py --folder test_datasets`
(`--dry-run` previews the target paths without connecting). Commit `chain.json` together with the new files.


| Option                 | Default         | Meaning                                                            |
| ---------------------- | --------------- | ------------------------------------------------------------------ |
| `--out`                | `src/data/raw`  | Where the four source files are written                            |
| `--log-dir`            | `src/data/.log` | Parent of the per-run log folder                                   |
| `--rows-per-file`      | `10000`         | Rows in *each* file (1,000–100,000)                                |
| `--defect-rate`        | `0.05`          | Fraction of rows per file with one injected defect (0–0.10)        |
| `--seed` / `--no-seed` | `20260915` / –  | Fixed seed, or draw a fresh one (printed and saved in the summary) |
| `--upload`             | off             | After generating, upload the new run to the Databricks volume (needs `.[databricks]`, `DATABRICKS_HOST`, `DATABRICKS_TOKEN`) |
| `--folder`             | `datasets`      | With `--upload`: `datasets` or `test_datasets` under `/Volumes/data2026acc123456/team3` |
| `--force-upload`       | off             | With `--upload`: skip the volume check that refuses conflicting runs (use with care) |
| `--continue`           | off             | Extend the delivered plant instead of building a new one (see below) |
| `--days`               | `28`            | With `--continue`: length of the new window, 21–90 days |
| `--base-dir`           | `src/data/raw`  | With `--continue`: folder with `chain.json` and the delivered files |




## Continuing the delivered plant

`--continue` reads the delivered files listed in `<base-dir>/chain.json` (the `*_2` baseline and every increment
recorded after it) and simulates the next `--days` days of the **same plant** on top of them. Per increment
(default 28 days: batch 1 = first 21 days, batch 2 = last 7 days, ~1,470 rows per file):

| Guarantee | How |
|---|---|
| Plant unchanged | No equipment file and no new assets; every row references an asset of the baseline (or carries a declared missing-key defect) |
| No id collisions | `WO-`, `MH-`, `SE-` numbering continues after the highest delivered id; new rows never repeat a delivered `(id, record_version)` |
| No downtime overlap with delivered data | Each asset's new downtime starts after its last delivered downtime ended; assets still down (no end delivered) get no new downtime in the window. The only overlaps are the deliberate `overlapping_downtime` defects *inside* the increment, listed in its `defect_manifest.csv` |
| Lineage does not collide | Batch ids and `source_file` stamps come from the window dates (`BATCH-<yyyymmdd>-01/-02`) |
| Checked every run | `continuation_check` re-verifies ids, references and downtime against the delivered data and fails the run on any conflict |

The run is appended to `chain.json` (when `--out` is `--base-dir`, the default), so the next `--continue` starts after
it. **Share `chain.json` together with the increment files** (commit both): the chain is what tells the next run, and the
upload check, which data already exists.

### Keeping the data acceptable to Silver

The Silver notebooks (`.claude/reference/test_pipeline.ipynb`) stop the whole run when an ERROR rule fires, so the
generated files must satisfy them. Two generator rules exist for that:

- **Labor cost** must equal `labor_hours x 55 / fixed rate` with Silver's fixed rates (EUR 1.0, GBP 1.17, USD 0.912; EUR to
  the cent, GBP/USD within 1.5 %), otherwise `LABOR_COST_CONSISTENT` aborts Silver. The generator uses monthly rates; the
  baseline months stay within about 1 % of the fixed ones, and months added by `--continue` are the fixed rate +/- 0.8 %
  (`SILVER_FIXED_FX`, `FX_MONTH_SPREAD`), never a drifting walk.
- **No activity on assets Silver equipment quarantines** (power < 0 or > 5000 kW, negative vibration limit, decommission
  before install): those assets get no new work orders, maintenance rows or sensor events.

Check a set of files against the real Silver code with `checks/silver_check.py` (see its docstring: needs Java and PySpark 3.5
in an ASCII-only environment on Windows). It runs the notebooks' cleaning functions without writing anything and exits 1
when an ERROR rule would fire; run it with `--tags 2` first as the control (the baseline must pass):

```bash
python src/data/generate/checks/silver_check.py --tags 2     # control: the baseline alone
python src/data/generate/checks/silver_check.py              # baseline + every increment in chain.json
```

Known limits: base-fleet sensor `base_load` is not delivered and is re-drawn per asset, so the 20 monitored assets can
show a small level shift at the boundary; degradation episodes are shorter in a window (about 3–18 days instead of
7–45); work orders still open or in progress at the end of the baseline stay as delivered (they are not closed by
later increments); windows shorter than 21 days cannot place the injected defects reliably.

## Output

Every run gets a UTC timestamp `<ts>` (`YYYYmmdd_HHMMSS`) so files never overwrite each other.

```
src/data/raw/                          only the four source datasets
  equipment_<ts>.csv                   asset master (one row per asset version)
  work_orders_<ts>.csv                 work orders (one row per version)
  maintenance_history_<ts>.csv         maintenance activities (one row per version)
  sensor_events_<ts>.json              IoT readings (JSON array, nested)
src/data/.log/<ts>/                    everything else from that run
  defect_manifest.csv                  every injected defect: file, row index, key, rule, expected handling
  generation_summary.json              seed, parameters, defect plan, row/batch counts
  batches/batch_01|02/                 the same four files split by delivery batch (for incremental loads)
  reference/                           failure modes, root causes, components, FX rates
  config/sensor_limits.csv             per-equipment-type sensor limits and valid ranges
```

Column contracts are in `docs/data/data_dictionary.md`.

## How it works

1. **Build the plant:** assets (9 equipment types, 2 sites) with type-specific ratings, parent/child links and
  SCD type-2 changes; 20 assets are IoT-monitored.
2. **Simulate events:** work orders (corrective, preventive, predictive, inspection) with SLAs, downtime and
  repeat failures; unplanned failures on monitored assets are preceded by 7–45 days of failure-mode-specific
   sensor degradation. Readings follow load, season and noise in SI units.
3. **Two delivery batches:** records are assigned to a batch by timestamp, which yields late-arriving events
  and corrected record versions in batch 2 (including a schema change in the sensor JSON).
4. **Inject defects:** exactly `defect-rate` of the rows in every file get one defect, split evenly over five
  rules per file (missing equipment key, out-of-range value, unit inconsistency, closure before creation,
   overlapping downtime, duplicates). Everything outside `defect_manifest.csv` is clean.
5. **Self-check:** defect counts are re-derived from the written files and compared to the manifest; the run
  fails if they differ.

Same seed + rows + defect rate give identical file *contents*; only the timestamp in the names changes.
Batch IDs, ingestion timestamps and the `source_file` audit column simulate the source systems' extracts and
are fixed, so they do not match the timestamped file names.