# Data-quality tests

**Spec:** §6.2 (DQ expectations, quarantine), §6.3 (Silver → Gold reconciliation)
**Evidence:** `docs/quality_evidence.md`

**Acceptance condition:** each Team 3 priority rule (out-of-range readings, unit inconsistency,
missing equipment keys, overlapping downtime, closure before creation, duplicate sensor events)
has a test proving that injected defects are detected and routed to quarantine, and reconciliation
tests prove Silver → Gold row counts and critical totals (downtime hours, maintenance cost) match.
Mark tests with `@pytest.mark.data_quality`.

**Status:** no tests here yet. The generator's own checks cover the injected defects for now: `self_check` in
`src/data/generate/generate_data.py` re-derives every defect count from the written files and compares it with
`defect_manifest.csv`, and `tests/unit/test_continuation.py` tests the continuation rules. The Silver rules themselves can be run
on the generated files with `src/data/generate/checks/silver_check.py` (needs Spark, not part of CI).
