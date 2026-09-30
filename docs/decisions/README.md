# Architecture decision records

**Spec:** §8 "Record architectural decisions and scope changes in the repository." · **Owner:** @participant5 (any member may author)

**Acceptance condition:** every material architectural decision or scope change has a numbered ADR
here with context, decision and consequences, merged via PR.

Copy [`0000-adr-template.md`](0000-adr-template.md) to `NNNN-short-title.md`.

## Candidate ADRs
- Bronze: incremental vs full refresh (§6.1)
- SCD type for `dim_equipment` (§6.2)
- Delta layout (partitioning / Z-order / liquid clustering) (§6.3)
- Fabric access pattern to Gold (§6.4)
- LLM provider and model (§6.5)
- API hosting service and auth mechanism (§6.6, §6.7)
- IaC tool: Bicep vs Terraform vs CLI (§6.6)
- `databricks/` folder is not a Python package (avoids shadowing `databricks-sdk`)

| # | Title | Status | Date |
|---|---|---|---|
