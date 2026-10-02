# Databricks

**Spec:** §6.1–6.3 · **Deliverable:** Databricks implementation → `docs/databricks_implementation.md`
**Owners:** @participant1 (common, bronze, silver, jobs), @participant2 (gold)

| Folder | Spec | Purpose |
|---|---|---|
| `common/` | §6.1–6.3 | Shared audit columns, record hash, run logging, config |
| `bronze/` | §6.1 | Raw, append-only Delta tables with ingestion metadata |
| `silver/` | §6.2 | Validated, standardized, deduplicated, conformed tables + quarantine |
| `golden/` | §6.3 | Star schema: facts, dimensions, aggregates |
| `jobs/` | §6.1, §7 | Job configuration for Bronze → Silver → Gold |

> This folder is intentionally **not** a Python package (no `__init__.py` here) so it does not
> shadow the `databricks` namespace used by `databricks-sdk` / Databricks Connect.
> See `docs/adr/`.
