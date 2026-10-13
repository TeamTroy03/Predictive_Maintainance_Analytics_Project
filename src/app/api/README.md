
FastAPI service over the Databricks **Gold** tables. It exposes governed KPIs, the page endpoints of the web
frontend (`src/app/frontend`), two domain endpoints, cookie login for the website, and the endpoint of the AI agent,
which may only use approved data functions.

```
Website ──/api (Vite proxy)──► FastAPI   Postman / scripts ──HTTPS + API key / JWT──► FastAPI
   /auth/*                                       ──► core/security.py (users from AUTH_USERS, session cookie)
   /fleet, /maintenance, /assets, /alarms        ──► dashboard.py, assets.py ──► kpis.py ──► db.py ──┐
   /analytics, /equipment, /work-orders          ──► services.py ───────────────────────────────────┤
                                                                       Databricks SQL warehouse ◄───┘ Gold
   /assistant/query, /assistant/approve ──► agent.py (adapter) ──► app.agent.agent ──► get_kpi / detail tool ──► Gold
```

## Files
| File | Role |
|---|---|
| `src/app/api/main.py` | Creates the app: middleware, error handlers, routes |
| `src/app/api/routes.py` | All endpoints (three routers: public, `/auth`, and the authenticated API) |
| `src/app/api/schemas.py` | Pydantic request/response models (validation + Swagger) |
| `src/app/api/services.py` | Queries on the Gold tables (KPIs, equipment health, overdue list), shared by the routes and the assistant |
| `src/app/api/kpis.py` | The 12 KPI formulas (same as Gold cell 3 and the DAX measures) |
| `src/app/api/dashboard.py` | Payloads of the Fleet Health and Maintenance pages, built from the governed KPIs and converted for display |
| `src/app/api/assets.py` | What the Equipment, Sensor Trends, Alarms and work-order pages read from the Gold tables |
| `src/app/api/shaping.py` | Helpers shared by the page endpoints (status, paging, value shaping) |
| `src/app/api/hash_password.py` | CLI that makes one `AUTH_USERS` entry for a website user (`python -m app.api.hash_password <email>`) |
| `src/app/api/db.py` | Databricks SQL warehouse connection: timeout, retries, bound parameters |
| `src/app/api/agent.py` | Adapter to the agent: `start_question`, `approve_run`, error and answer mapping |
| `src/app/api/security.py` | `require_auth` dependency: Entra ID JWT (deployed), session cookie (website) or API key (dev) |
| `src/app/api/errors.py` | One JSON error format for every failure |
| `src/app/api/observability.py` | Correlation-ID request middleware |
| `src/app/core/config.py` | Settings from environment variables (Key Vault in Azure) |
| `src/app/core/security.py` | Password hashing, login throttle, session token, Entra ID JWT checks |
| `src/app/core/logging.py` | JSON logs (secrets redacted) and the `correlation_id` context variable |

## Endpoints
Auth: ✔ = needs a valid session cookie, an `X-API-Key` (dev) or an Entra ID bearer token (see [Website login](#website-login)).

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/health` | – | Liveness |
| GET | `/ready` | – | Readiness: Gold reachable (`gold`) and auth configured (`auth_configured`); 503 if not |
| POST | `/auth/login` | – | Website sign-in with email and password; sets the session cookie (429 after 5 failures in 15 min, 503 if login is not configured) |
| POST | `/auth/logout` | – | Clears the session cookie |
| GET | `/auth/me` | cookie | The signed-in website user (401 for anyone else, also for an API key) |
| GET | `/kpis` | ✔ | The 12 governed KPIs |
| POST | `/analytics/query` | ✔ | KPIs with group_by (site, equipment_type, equipment, month) and filters |
| GET | `/fleet/summary` | ✔ | Fleet Health page: 8 KPIs, asset status counts, availability trend |
| GET | `/maintenance/summary` | ✔ | Maintenance page: KPIs, monthly MTBF/MTTR/cost, cost by maintenance type |
| GET | `/assets` | ✔ | Assets with status, most urgent first (paginated: `page`, `page_size` up to 100; filters `status`, `q`, `has_sensors`) |
| GET | `/assets/{equipment_id}` | ✔ | One asset: status, KPIs, latest sensor readings |
| GET | `/assets/{equipment_id}/sensors` | ✔ | Sensor series of one asset (`range` 1h, 24h (default), 7d, 30d or all, counted back from its last reading) |
| GET | `/alarms` | ✔ | Alarm readings, newest first (`asset_id`, `limit` up to 500) |
| GET | `/maintenance/work-orders` | ✔ | Work orders: overdue first, then open, then the rest (`status`, `asset_id`, `limit` up to 500) |
| GET | `/equipment/{equipment_id}/health` | ✔ | One asset's KPIs, latest alarms, open work orders |
| GET | `/work-orders/overdue` | ✔ | Overdue work orders, paginated |
| POST | `/assistant/query` | ✔ | Step 1: question in natural language (optional `conversation_id` for follow-up memory); returns the agent's plan awaiting approval |
| POST | `/assistant/approve` | ✔ | Step 2: approve or decline the plan; returns the answer |

Gold has no alarm acknowledgements, so there is no acknowledge endpoint.

## Run
1. In Databricks: **SQL Warehouses → your warehouse → Connection details** gives the host and
   HTTP path; **User settings → Developer → Access tokens** gives a token. The warehouse must be
   able to read the `test_gold_*` tables (Unity Catalog `SELECT` on schema `team3`).
2. Then:
```bash
uv sync --extra dev                # creates .venv with the locked dependencies
cp .env.example .env               # fill in the Databricks values and an API key
uv run uvicorn app.api.main:app --reload   # run from the repo root; Swagger UI: http://127.0.0.1:8000/docs
```
3. Check `GET /ready` → `"gold": true`, then try the Postman collection (`postman/team3-pm-api.postman_collection.json`).
4. For the website, add a user and start the frontend (see [Website login](#website-login) and `src/app/frontend/README.md`).
5. Optional, for the assistant: Redis (conversation memory) and Neo4j (knowledge graph). Start them **before** the API:

```bash
# Redis: conversation memory (follow-up questions). Neo4j: store of the knowledge graph used by the assistant's query_graph tool.
docker run -d --name pm-redis -p 6379:6379 redis:7-alpine
docker run -d --name pm-neo4j -p 127.0.0.1:7687:7687 -e NEO4J_AUTH=neo4j/<password> neo4j:5
# next days: docker start pm-redis pm-neo4j      stop: docker stop pm-redis pm-neo4j      remove: docker rm -f pm-redis pm-neo4j

# wait until both answer (Neo4j needs 10-20 s after it starts), otherwise the startup graph sync below fails once
docker exec pm-redis redis-cli ping                                          # PONG
docker exec pm-neo4j cypher-shell -u neo4j -p <password> "RETURN 1"          # prints 1
```

Add to `.env` (both services are optional: without them the memory stays in the process and the graph is built in memory):

```
REDIS_URL=redis://localhost:6379/0
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<the password of docker run>
GRAPH_TABLE_PREFIX=test_            # prefix of the gold_kg_* tables if it differs from GOLD_TABLE_PREFIX
```

When the API starts and `NEO4J_URI` is set, it copies the Gold knowledge graph tables into Neo4j in a background thread (`graph_sync_started` / `graph_sync_finished` in the log). It never blocks or fails the start, it only loads when Gold has a newer load than the copy in Neo4j (`GRAPH_SYNC_ON_STARTUP=false` switches it off), and until it has finished the assistant answers graph questions from an in-memory graph. A Gold run while the API stays up is only picked up after a restart or a manual `uv run python -m app.agent.tools.graph_sync`.

## Configuration
The API reads environment variables or the `.env` in the working directory (run uvicorn from the
repo root). The Databricks and Gold variables are the same ones the agent uses, so one `.env`
serves both. There is no `PM_` prefix.

| Variable | Used for |
|---|---|
| `DATABRICKS_HOST`, `DATABRICKS_HTTP_PATH`, `DATABRICKS_TOKEN` | SQL warehouse connection (shared with the agent) |
| `GOLD_CATALOG`, `GOLD_SCHEMA`, `GOLD_TABLE_PREFIX` | Table names `<catalog>.<schema>.<prefix>gold_<name>` (shared with the agent) |
| `API_KEYS` | Comma-separated API keys for dev auth (for scripts and Postman) |
| `JWT_TENANT_ID`, `JWT_AUDIENCE`, `JWT_REQUIRED_ROLE` | Entra ID JWT for the deployed design (optional; role defaults to `PM.Reader`) |
| `AUTH_USERS` | Website users, `email=hash;email2=hash2` (make an entry with `python -m app.api.hash_password <email>`) |
| `JWT_SECRET` | Signs the session token, **at least 32 characters** (cookie login stays off with a shorter or empty secret) |
| `SESSION_MINUTES` | Lifetime of the session cookie (default 60) |
| `COOKIE_SECURE` | `true` (default): the cookie is sent over HTTPS only. Set `false` only for a browser that refuses Secure cookies on localhost |
| `ENVIRONMENT` | `dev` (default) or `prod`; `prod` refuses to start unless `COOKIE_SECURE` is true, `JWT_SECRET` is long enough and `AUTH_USERS` is set |
| `QUERY_TIMEOUT_SECONDS`, `QUERY_RETRIES` | Databricks query limits (defaults 30 and 2) |
| `REDIS_URL`, `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`, `GRAPH_TABLE_PREFIX`, `GRAPH_SYNC_ON_STARTUP` | Optional services, read from the environment or `.env` by the agent code, not by `Settings`. With `REDIS_URL` the page endpoints (`/fleet/summary`, `/maintenance/summary`, `/assets`) also keep their 60 s response cache in Redis (`app/core/cache.py`, shared by replicas and kept over restarts); without it the cache lives in the process. The browser keeps no cache. See `src/app/agent/README.md` |

## Website login
The website signs in with email and password. There is no registration: users are added by hand.

1. `python -m app.api.hash_password someone@example.com` asks for a password (at least 12 characters) and prints one entry
   `someone@example.com=scrypt$...`.
2. Put the entries in `AUTH_USERS` (separated by `;`) in `.env` (Key Vault when deployed), plus a random `JWT_SECRET` of 32+
   characters. `.env.example` does not list these variables yet.
3. `POST /auth/login` checks the password, throttles repeated failures (5 per 15 minutes per client and email) and sets the
   `access_token` cookie: a JWT signed with `JWT_SECRET`, `HttpOnly`, `SameSite=Strict`, `Secure` unless `COOKIE_SECURE=false`.
   JavaScript never sees the token. `require_auth` accepts, in this order, an Entra ID bearer token, the session cookie, or an API key.

## Assistant (agent)
The agent (`src/app/agent/agent.py`) plans, reviews its plan, and **pauses until a human approves it**
before any tool runs, so the assistant has two steps:

1. `POST /assistant/query` `{"question": "...", "conversation_id": "..."}` → `status: "awaiting_approval"`, the `plan` and a `run_id`.
   `conversation_id` is optional (`[A-Za-z0-9._-]`, up to 64 characters): questions with the same ID share the
   agent's memory of earlier answers, so a follow-up like "and for pumps?" works (see the agent README).
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
- The assistant has three tools: `get_kpi`, `query_kpi_table` and `query_graph` (relationship questions over the knowledge graph, Neo4j with an in-memory fallback). The result of a graph question says which backend answered.
- **Client timeouts:** each question makes several sequential LLM calls (plan, review, summary, answer, and up
  to 2 plan revisions), each limited by `LLM_TIMEOUT_SECONDS` (60) with `LLM_MAX_RETRIES` (2) retries. Typical
  time is tens of seconds, the worst case is minutes. Use a client timeout of about 300 s for both assistant
  calls: Postman Settings → Request timeout, `newman --timeout-request 300000`, `curl --max-time 300`.


