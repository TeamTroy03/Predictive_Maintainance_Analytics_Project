# 02 — Architecture and lineage

**Spec:** §7 Deliverable "Architecture and lineage"; §5 Target Architecture · **Owners:** @participant4, @participant5

**Required evidence:** source-to-report-to-API diagram covering Databricks, Fabric, FastAPI,
LangChain and Azure resources.

## Architecture diagram
_TODO: Sources → Bronze → Silver → Gold → Fabric semantic model → Power BI; Gold/semantic → FastAPI → LangChain tools; Azure RG, Key Vault, identity, CI/CD._

## Layer responsibilities
| Layer | Tables / components | Purpose |
|---|---|---|
| Sources | | |
| Bronze | | |
| Silver | | |
| Gold | | |
| Fabric / Power BI | | |
| FastAPI | | |
| LangChain Core | | |
| Azure | | |
| CI/CD | | |

## Lineage example
_TODO: trace one KPI value from report visual → DAX measure → Gold table → Silver → Bronze batch → source file._

## Cross-cutting capabilities
Environment-based configuration · secrets outside source control · RBAC · lineage · job monitoring ·
automated tests · version control · reproducible deployment.
