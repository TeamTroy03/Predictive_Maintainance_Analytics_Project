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
```


| Option                 | Default         | Meaning                                                            |
| ---------------------- | --------------- | ------------------------------------------------------------------ |
| `--out`                | `src/data/raw`  | Where the four source files are written                            |
| `--log-dir`            | `src/data/.log` | Parent of the per-run log folder                                   |
| `--rows-per-file`      | `10000`         | Rows in *each* file (1,000–100,000)                                |
| `--defect-rate`        | `0.05`          | Fraction of rows per file with one injected defect (0–0.10)        |
| `--seed` / `--no-seed` | `20260915` / –  | Fixed seed, or draw a fresh one (printed and saved in the summary) |




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