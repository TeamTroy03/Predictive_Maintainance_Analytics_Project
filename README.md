# Team 3 — Predictive Maintenance Analytics

AI and Data Capstone (Accenture / Code.Hub). Spec: [`docs/spec/`](docs/spec/).

An industrial company needs to understand equipment health, abnormal sensor behavior,
maintenance effectiveness, downtime and recurring failure modes.

## Solution path

`data/` (CSV + JSON) → Databricks Bronze → Silver → Gold (Delta star schema)
→ Microsoft Fabric semantic model → Power BI report,
with FastAPI + LangChain Core exposing approved analytical tools, hosted on Azure and delivered via GitHub Actions.

## Spec → repository map

| Spec | Scope | Code | Docs |
|---|---|---|---|
| §4 | Dataset | `data/` | `docs/01-business-data-brief/` |
| §6.1 | Bronze ingestion | `databricks/bronze/`, `databricks/common/` | `docs/03-databricks-implementation.md` |
| §6.2 | Silver preparation | `databricks/silver/`, `tests/data_quality/` | `docs/04-quality-evidence/` |
| §6.3 | Gold dimensional model | `databricks/gold/` | `docs/05-dimensional-model/` |
| §6.4 | Fabric semantic model & report | `fabric/` | `docs/06-fabric-powerbi.md` |
| §6.5 | LangChain Core | `app/assistant/` | `docs/07-langchain-integration.md` |
| §6.6 | Azure fundamentals | `infra/` | `docs/09-azure-design.md` |
| §6.7 | REST / FastAPI | `app/api/`, `postman/` | `docs/08-fastapi-integration.md` |
| §6.8 | CI/CD | `.github/workflows/`, `Dockerfile` | `docs/10-cicd-pipeline.md` |
| §7 | Testing & operations | `tests/` | `docs/11-testing-operations.md` |
| §8 | Project management | — | `docs/project-management/`, `docs/decisions/` |

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
