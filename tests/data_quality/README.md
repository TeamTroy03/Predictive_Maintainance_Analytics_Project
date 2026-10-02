# Data-quality tests

**Spec:** §6.2 (DQ expectations, quarantine), §6.3 (Silver → Gold reconciliation) · **Owners:** @participant1, @participant2
**Evidence:** `docs/quality_evidence.md`

**Acceptance condition:** each Team 3 priority rule (out-of-range readings, unit inconsistency,
missing equipment keys, overlapping downtime, closure before creation, duplicate sensor events)
has a test proving that injected defects are detected and routed to quarantine, and reconciliation
tests prove Silver → Gold row counts and critical totals (downtime hours, maintenance cost) match.
Mark tests with `@pytest.mark.data_quality`.
