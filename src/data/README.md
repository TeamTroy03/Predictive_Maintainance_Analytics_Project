# Data

**Spec:** §4 Dataset · **Deliverable:** Business and data brief → `docs/business_brief.md`

**Acceptance condition (§4 minimums):**
- Transactional CSV: `maintenance_history`, `work_orders` (related, with dates, IDs, costs/durations)
- Reference CSV: `equipment`
- Semi-structured JSON: `sensor_events`
  (files are named `<dataset>_<tag>.<ext>`: `_2` for the baseline, a timestamp for every later increment)
- ≥ 10,000 transactional/event records in total
- Deliberately injected quality defects (see below)
- Synthetic only: no real personal or confidential information

## Layout

| Folder | Contents |
|---|---|
| `raw/` | The delivered source files (baseline `*_2` + increments) and `chain.json`, the index of what was delivered |
| `generate/` | `generate_data.py` (seeded generator, `--continue` for new activity on the delivered plant), `base_state.py` (reads the delivered files), `checks/silver_check.py` (runs the Silver notebooks' rules on the files) |
| `upload/` | Uploads one generated run to the Databricks volume (`team3/datasets` or `team3/test_datasets`) |

`raw/chain.json` lists the delivered files (baseline `*_2` + increments). New data must be a **continuation** of that
plant (`generate_data.py --continue`), never a second new plant; see `generate/README.md`.

## Defects to inject (Team 3 priority DQ rules)

- Out-of-range sensor readings
- Unit inconsistency (e.g. °C vs °F, bar vs psi)
- Missing / unknown equipment keys
- Overlapping downtime intervals
- Work order closed before it was created
- Duplicate sensor events

## Uploading to Databricks

Each run goes to one sub-folder per dataset, e.g.
`/Volumes/data2026acc123456/team3/test_datasets/work_orders/work_orders_<ts>.csv`.

```bash
uv sync --extra dev --extra databricks                                       # adds databricks-sdk to the project environment
uv run python src/data/generate/generate_data.py --continue --upload --folder test_datasets  # new activity, then upload
uv run python src/data/upload/upload_to_databricks.py --folder test_datasets     # upload the latest run in raw/
uv run python src/data/upload/upload_to_databricks.py --dry-run                  # show the mapping only
```

Before uploading, the volume is checked (skipped with `--dry-run`, overridden with `--force`): a new plant is refused when
the folder already holds another run, and a continuation must be recorded in `chain.json` with every run already in the
volume known to it. A continuation has no equipment file, so it uploads three datasets. If the baseline sits in the volume
under another name (e.g. `equipment_20261003_101847.csv`), add that tag to `baseline.aliases` in `chain.json`.

Needs `DATABRICKS_HOST` and `DATABRICKS_TOKEN` (environment or `.env`). `--folder` is `datasets` (default) or
`test_datasets`; `DATABRICKS_VOLUME_PATH` / `--volume-path` set a full custom path.
