# Team 3 — Predictive Maintenance Analytics

AI and Data Capstone (Accenture / Code.Hub). Spec: [`context/`](context/).

An industrial company needs to understand equipment health, abnormal sensor behavior,
maintenance effectiveness, downtime and recurring failure modes.

## Solution path

`src/data/` (CSV + JSON) → Databricks Bronze → Silver → Gold (Delta star schema)
→ Microsoft Fabric semantic model → Power BI report,
with FastAPI + LangChain Core exposing approved analytical tools, hosted on Azure and delivered via GitHub Actions.

## Spec → repository map

| Spec | Scope | Code | Docs |
|---|---|---|---|
| §4 | Dataset | `src/data/` | `docs/business_brief.md` |
| §6.1 | Bronze ingestion | `databricks/bronze/`, `databricks/common/` | `docs/databricks_implementation.md` |
| §6.2 | Silver preparation | `databricks/silver/`, `tests/data_quality/` | `docs/quality_evidence.md` |
| §6.3 | Gold dimensional model | `databricks/golden/` | `docs/dimensional_model.md` |
| §6.4 | Fabric semantic model & report | `fabric/` | `docs/fabric_powerbi.md` |
| §6.5 | LangChain Core | `src/app/agent/` | `docs/langchain_integration.md` |
| §6.6 | Azure fundamentals | `infra/` | `docs/azure_design.md` |
| §6.7 | REST / FastAPI | `src/app/api/`, `postman/` | `docs/fastapi_integration.md` |
| §6.8 | CI/CD | `.github/workflows/`, `Dockerfile` | `docs/cicd.md` |
| §7 | Testing & operations | `tests/` | `docs/runbook.md` |
| §8 | Project management | — | `docs/project_management.md`, `docs/adr/` |

## Repository layout

```text
.github/workflows/   ci.yml (lint, test, image build) · deploy.yml (approval-gated)
context/             original spec PDF
databricks/          common/ · bronze/ · silver/ · golden/ · jobs/  (not a Python package)
docs/                one flat .md per deliverable · adr/ · diagrams/
fabric/              semantic-model/ · report/  (PBIP / TMDL source)
infra/bicep-scripts/ main.bicep + dev/prod .bicepparam
postman/             collection.json · environment.json
src/app/
  api/               main.py · routes/ (one router per endpoint group) · schemas/ (Pydantic)
  agent/             LangChain Core: agent.py · schemas.py · prompts/ · tools/
  core/              config · logging · security
  services/          business logic shared by routes and agent tools
src/data/            generate/ (seeded generator) · raw/ (source files for Bronze)
tests/               unit/ · data_quality/ · integration/
```

## Team (§8)

| Handle | Role |
|---|---|
| @participant1 | Data engineering |
| @participant2 | Analytics |
| @participant3 | AI and API |
| @participant4 | Cloud and DevOps |
| @participant5 | Project manager |

## Local development

Requires Python 3.14+.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env                                # fill in locally; never commit .env
ruff check . && pytest
uvicorn app.api.main:app --reload                   # http://localhost:8000/docs
```
