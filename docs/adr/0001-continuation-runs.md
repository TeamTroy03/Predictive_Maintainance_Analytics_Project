# 0001: New data continues the delivered plant

* Status: accepted
* Date: 2026-10-12
* Owner: data engineering

## Context

`generate_data.py` simulated a complete new plant on every run: ids restarted (`EQ-10001`, `WO-26001`, `MH-00001`,
`SE-000001`), the span was always 2026-03-15 .. 2026-09-22 and the batch ids were fixed. Bronze (Auto Loader) appends
every file in the volume and Silver merges by business key and `record_version`, so a second run reused keys for different
assets and overlapped the first run's downtime on the same asset. The demo needs new data to arrive on top of what is
already loaded.

## Decision

* The delivered `src/data/raw/*_2.*` files are the baseline plant. The plant does not change: new data only adds
  activity (work orders, maintenance history, sensor events), never new assets and no equipment file.
* `generate_data.py --continue` simulates the next window (default 28 days) for that plant. The delivered files, listed
  in `raw/chain.json`, are the only state (no separate state file that could go stale): ids continue after the delivered
  maximum, each asset's new downtime starts after its last delivered downtime ended, assets still down are blocked, and
  `continuation_check` verifies this against the delivered data on every run.
* The upload to the volume checks the volume first: a new plant is refused when another run is there; a continuation must
  be in `chain.json` and every run in the volume must be known to it (`--force` overrides).

* Generated files must also pass the Silver notebooks' ERROR rules (they stop a whole Silver run). In particular labor cost
  follows Silver's fixed FX rates (extended months stay within 0.8 % of them) and assets that Silver equipment quarantines
  receive no new activity. `src/data/generate/checks/silver_check.py` runs the notebooks' own cleaning code over the files to prove it.

## Consequences

* Runs after the baseline are conflict-free by construction and by check; two people cannot silently create the same window
  if they share `chain.json` and the increment files.
* Increments are small (~1,470 rows per file at 28 days) and refresh quickly in the demo.
* Not covered: work orders still open at the end of a window are never closed by a later increment (they stay as delivered),
  so the open/overdue counts only grow; the 20 monitored assets' sensor level can shift slightly at a boundary; windows
  below 21 days cannot place the injected defects reliably.
* Alternatives rejected: a persisted simulation state file (stale across machines); one long deterministic plant cut into
  slices (changes the baseline, which the team already loaded); replacing the previous run on every upload (no incremental
  story).
