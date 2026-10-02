# Microsoft Fabric and Power BI

**Spec:** §6.4 Microsoft Fabric: Semantic Model and Report · **Owner:** @participant2
**Deliverable:** Fabric and Power BI → `docs/fabric_powerbi.md`

**Acceptance condition (§6.4 / §7):** a governed semantic model over Gold with relationships,
hierarchies, descriptions and a measures table; ≥ 8 explicit DAX measures (volume, rate/ratio,
trend, exception); a proper date table; RLS where ownership boundaries exist (by site);
≥ 3 report pages; report totals validated against Gold.

| Folder | Contents |
|---|---|
| `semantic-model/` | Semantic model source (PBIP / TMDL) |
| `report/` | Report source (PBIP) |

Commit PBIP/TMDL source, not `.pbix` binaries, so changes are reviewable in PRs.
