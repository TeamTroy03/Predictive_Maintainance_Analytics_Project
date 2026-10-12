
FastAPI service over the Databricks **Gold** tables. It exposes governed KPIs, two domain
endpoints, and the endpoint of the AI agent, which may only use approved data functions.

```
Client (Postman, app, agent UI) ──HTTPS + API key / JWT──► FastAPI
   /analytics, /equipment, /work-orders ──► services.py ──► db.py ──► Databricks SQL warehouse ──► Gold
   /assistant/query, /assistant/approve ──► agent.py (adapter) ──► app.agent.agent ──► get_kpi / detail tool ──► Gold
```

## Files
| File | Role |
|---|---|
| `src/app/api/main.py` | Creates the app: middleware, error handlers, routes |
| `src/app/api/routes.py` | All endpoints |
| `src/app/api/schemas.py` | Pydantic request/response models (validation + Swagger) |
| `src/app/api/services.py` | Queries on the Gold tables (KPIs, equipment health, overdue list) |
| `src/app/api/kpis.py` | The 12 KPI formulas (same as Gold cell 3 and the DAX measures) |
| `src/app/api/db.py` | Databricks SQL warehouse connection: timeout, retries, bound parameters |
| `src/app/api/agent.py` | Adapter to the agent: `start_question`, `approve_run`, error and answer mapping |
| `src/app/api/security.py` | `require_auth` dependency: cookie, API key (dev) or Entra ID JWT (deployed) |
| `src/app/api/errors.py` | One JSON error format for every failure |
| `src/app/api/observability.py` | Correlation-ID request middleware |
| `src/app/core/config.py` | Settings from environment variables (Key Vault in Azure) |
| `src/app/core/security.py` | Password hashing, login throttle, session token, Entra ID JWT checks |
| `src/app/core/logging.py` | JSON logs (secrets redacted) and the `correlation_id` context variable |

## Endpoints
| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | – | Liveness |
| GET | `/ready` | – | Readiness: Databricks reachable and auth configured (503 if not) |
| GET | `/kpis` | ✔ | The 12 governed KPIs |
| POST | `/analytics/query` | ✔ | KPIs with group_by (site, equipment_type, equipment, month) and filters |
| GET | `/equipment/{id}/health` | ✔ | One asset's KPIs, latest alarms, open work orders |
| GET | `/work-orders/overdue` | ✔ | Overdue work orders, paginated |
| POST | `/assistant/query` | ✔ | Step 1: question in natural language; returns the agent's plan awaiting approval |
| POST | `/assistant/approve` | ✔ | Step 2: approve or decline the plan; returns the answer |

## Run
1. In Databricks: **SQL Warehouses → your warehouse → Connection details** gives the host and
   HTTP path; **User settings → Developer → Access tokens** gives a token. The warehouse must be
   able to read the `test_gold_*` tables (Unity Catalog `SELECT` on schema `team3`).
2. Then:
```bash
pip install -e ".[dev]"
cp .env.example .env               # fill in the Databricks values and an API key
uvicorn app.api.main:app --reload      # run from the repo root; Swagger UI: http://127.0.0.1:8000/docs
```
3. Check `GET /ready` → `"gold": true`, then try the Postman collection.

## Configuration
The API reads environment variables or the `.env` in the working directory (run uvicorn from the
repo root). The Databricks and Gold variables are the same ones the agent uses, so one `.env`
serves both. There is no `PM_` prefix.

| Variable | Used for |
|---|---|
| `DATABRICKS_HOST`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN` | SQL warehouse connection (shared with the agent) |
| `GOLD_CATALOG`, `GOLD_SCHEMA`, `GOLD_TABLE_PREFIX` | Table names `<catalog>.<schema>.<prefix>gold_<name>` (shared with the agent) |
| `API_KEYS` | Comma-separated API keys for dev auth (required unless JWT is configured) |
| `JWT_TENANT_ID`, `JWT_AUDIENCE`, `JWT_REQUIRED_ROLE` | Entra ID JWT for the deployed design (optional; role defaults to `PM.Reader`) |
| `ENVIRONMENT` | `dev` (default) or `prod` |
| `QUERY_TIMEOUT_SECONDS`, `QUERY_RETRIES` | Databricks query limits (defaults 30 and 2) |

## Assistant (agent)
The agent (`src/app/agent/agent.py`) plans, reviews its plan, and **pauses until a human approves it**
before any tool runs, so the assistant has two steps:

1. `POST /assistant/query` `{"question": "..."}` → `status: "awaiting_approval"`, the `plan` and a `run_id`.
   (If the plan is rejected as out of scope nothing runs and the response is already `status: "answered"`
   with the reason in `limitations`.)
2. `POST /assistant/approve` `{"run_id": "...", "approved": true}` → `status: "answered"` with the
   `result` (answer, evidence per tool call, grounded, limitations, filters, source_measure).
   `approved: false` declines the plan. Errors: 404 `unknown_run`, 409 `run_not_pending`, 429 `rate_limited`,
   503 `assistant_unavailable`.

Things to know:
- The `run_id` is the correlation ID of the query request (send your own `X-Correlation-ID` to choose it;
  a reused ID gets a fresh one). Send `X-Correlation-ID: <run_id>` on the approve call to trace both steps,
  and find the agent's log lines with that ID in `src/app/agent/.log/agent.log`.
- The agent needs `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT_NAME` and
  `OPENAI_API_VERSION`. It is imported on the first assistant call, so the API starts without them and the
  assistant answers 503 until they are set.
- A pending plan lives in memory (`MemorySaver`): run **one uvicorn worker**; a restart loses pending plans.
- **Client timeouts:** each question makes several sequential LLM calls (plan, review, summary, answer, and up
  to 2 plan revisions), each limited by `LLM_TIMEOUT_SECONDS` (60) with `LLM_MAX_RETRIES` (2) retries. Typical
  time is tens of seconds, the worst case is minutes. Use a client timeout of about 300 s for both assistant
  calls: Postman Settings → Request timeout, `newman --timeout-request 300000`, `curl --max-time 300`.


