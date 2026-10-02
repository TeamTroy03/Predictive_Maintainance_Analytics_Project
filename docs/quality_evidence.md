# 04 — Quality evidence

**Spec:** §7 Deliverable "Quality evidence"; §6.2, §6.3 · **Owners:** @participant1, @participant2

**Required evidence:** rules, test results, quarantine examples and reconciliation summary.

## Rules
| Rule ID | Rule | Dimension | Table | Action | Defects injected | Defects caught |
|---|---|---|---|---|---|---|
| DQ-01 | Out-of-range readings | Validity | | Quarantine | | |
| DQ-02 | Unit inconsistency | Validity | | Convert / quarantine | | |
| DQ-03 | Missing equipment keys | Referential integrity | | Unknown member / quarantine | | |
| DQ-04 | Overlapping downtime | Validity | | Quarantine | | |
| DQ-05 | Closure before creation | Validity | | Quarantine | | |
| DQ-06 | Duplicate sensor events | Uniqueness | | Deduplicate | | |

## Test results
_TODO: link CI run / pytest output for `tests/data_quality/`._

## Quarantine examples
_TODO: sample rows with reason, rule and processing timestamp, and how each is resolved._

## Reconciliation
See [`reconciliation.md`](reconciliation.md).
