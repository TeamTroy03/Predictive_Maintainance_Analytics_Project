# Microsoft Fabric and Power BI

**Spec:** §6.4 Microsoft Fabric: Semantic Model and Report
**Deliverable:** Fabric and Power BI → `docs/fabric_powerbi.md`

**Acceptance condition (§6.4 / §7):** a governed semantic model over Gold with relationships,
hierarchies, descriptions and a measures table; ≥ 8 explicit DAX measures (volume, rate/ratio,
trend, exception); a proper date table; RLS where ownership boundaries exist (by site);
≥ 3 report pages; report totals validated against Gold.

| Folder | Contents |
|---|---|
| `semantic-model/` | Semantic model. Today: relationship diagrams (PNG) of the model over the `test_gold_*` tables; the PBIP / TMDL source is still to come |
| `report/` | Report source (PBIP). Nothing committed yet |

Commit PBIP/TMDL source, not `.pbix` binaries, so changes are reviewable in PRs.
