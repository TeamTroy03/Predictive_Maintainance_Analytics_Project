
FastAPI service over the Databricks **Gold** tables. It exposes governed KPIs, two domain
endpoints, and the endpoint of the AI agent, which may only use approved data functions.

```
Client (Postman, app, agent UI) ──HTTPS + API key / JWT──► FastAPI
   /analytics, /equipment, /work-orders ──► services.py ──► db.py ──► Databricks SQL warehouse ──► Gold
   /assistant/query ──► agent.py (your agent) ──► AgentTools = the same services
```

## Files
| File | Role |
|---|---|
| `app/main.py` | Creates the app: middleware, error handlers, routes |
| `app/routes.py` | All endpoints |
| `app/schemas.py` | Pydantic request/response models (validation + Swagger) |
| `app/services.py` | Queries on the Gold tables (KPIs, equipment health, overdue list) |
| `app/kpis.py` | The 12 KPI formulas (same as Gold cell 3 and the DAX measures) |
| `app/db.py` | Databricks SQL warehouse connection: timeout, retries, bound parameters |
| `app/agent.py` | **Connection point for the agent** (`answer_question`) and its tools |
| `app/security.py` | API key (dev) and Entra ID JWT (deployed) |
| `app/errors.py` | One JSON error format for every failure |
| `app/observability.py` | Correlation IDs and JSON logs (secrets redacted) |
| `app/config.py` | Settings from environment variables (Key Vault in Azure) |

## Endpoints
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | – | Liveness |
| GET | `/ready` | – | Readiness: Databricks reachable and auth configured (503 if not) |
| GET | `/kpis` | ✔ | The 12 governed KPIs |
| POST | `/analytics/query` | ✔ | KPIs with group_by (site, equipment_type, equipment, month) and filters |
| GET | `/equipment/{id}/health` | ✔ | One asset's KPIs, latest alarms, open work orders |
| GET | `/work-orders/overdue` | ✔ | Overdue work orders, paginated |
| POST | `/assistant/query` | ✔ | Question in natural language, answered by the agent |

## Run
1. In Databricks: **SQL Warehouses → your warehouse → Connection details** gives the host and
   HTTP path; **User settings → Developer → Access tokens** gives a token. The warehouse must be
   able to read the `test_gold_*` tables (Unity Catalog `SELECT` on schema `team3`).
2. Then:
```bash
pip install -r requirements.txt
cp .env.example .env               # fill in the Databricks values and an API key
uvicorn app.main:app --reload      # Swagger UI: http://127.0.0.1:8000/docs
```
3. Check `GET /ready` → `"gold": true`, then try the Postman collection.

## Connecting the agent
Implement `answer_question(question, tools)` in `app/agent.py`. `tools` has
`get_kpis`, `get_equipment_health` and `list_overdue_work_orders`: wrap them as LangChain
tools, let the model call them, and return an `AssistantAnswer` with the answer and the
evidence of each tool call. Until then `/assistant/query` returns 503 `assistant_unavailable`.


