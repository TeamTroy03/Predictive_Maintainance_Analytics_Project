# Plan: GitHub Actions CI, offline tests for `src/app`

## Context
Spec §6.8 needs a pipeline that runs on PRs and `main` and gates the code automatically. `.github/workflows/` does not exist yet.

Scope, as you set it:
- **Offline tests only.** Their job is to check that the scripts work with **template data** (canned rows and a fake LLM). They need no Azure or Databricks secrets.
- **No lint job** (it fails on the existing code).
- **No infra and no CD** (no Bicep, ACR, deploy, approvals or Docker push).
- **Included:** frontend build, security tests, agent-behaviour tests, and API contract tests written as concrete HTTP requests.

The existing 90+ unit tests in `tests/unit` already run offline. This plan adds the missing blocks, grouped as you asked.

## 0. Small fixes so the suite is green in CI
| # | Problem | Fix |
|---|---|---|
| P1 | `test_detail_tools.py::test_get_kpi_schema` is stale (`describe()` now takes `{table: schema}`) | Fix the fixture |
| P2 | 3 `test_auth.py` tests fail in a full run: `agent.py` calls `load_dotenv()` and leaks `.env` values (`COOKIE_SECURE=false`) into `os.environ` | Autouse fixture in `tests/unit/conftest.py` that clears `AUTH_USERS`, `JWT_SECRET`, `COOKIE_SECURE`, `API_KEYS`, `JWT_TENANT_ID`, `JWT_AUDIENCE`, `ENVIRONMENT`. CI has no `.env`, so this only matters locally. |
| P3 | `src/app/agent/tools/.tests/test_kpi_tools.py` is outside `testpaths` and needs live services | Move it to `tests/integration/` (stays out of CI) |

## 1. Workflow: `.github/workflows/ci.yml` (on `pull_request` and `push: main`)
Two parallel jobs, no secrets, `permissions: contents: read`:

- **`test`**: `astral-sh/setup-uv` (Python from `.python-version`), `uv sync --locked --extra dev`, then `uv run pytest -m "not integration" tests/unit` with dummy env vars set for the agent import (`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT_NAME`, `OPENAI_API_VERSION`, `GOLD_CATALOG`, `GOLD_SCHEMA`, `AGENT_LOG_DIR=$RUNNER_TEMP/agent-log`). Upload `junit.xml` as an artifact.
- **`frontend`**: `npm ci && npm run build` in `src/app/frontend` (it has no lint or test script, so a successful build is the check).

Seams that make this possible without credentials: `app.dependency_overrides[get_db]` (fake `GoldDB` with canned rows), `create_app(Settings(...))`, and patching `agent._ask` / `plan` / `judge_plan`, `detail_tools.run_rows` and `kpi_tools.compute_kpi(run=fake)`.

## 2. Test blocks to add (all in `tests/unit`, template data only)

### A. Security
1. **`check_sql` hostile corpus**: writes, stacked statements, `read_files`, system tables, `EXPLAIN`, `COPY`, `SET`, `UNION` with another table, and the **confirmed bypass** `WITH secrets AS (SELECT * FROM secrets) SELECT * FROM secrets` (a self-referencing CTE passes today), plus `ai_query` / `reflect`. Start the known bypasses as `xfail(strict=True)`, then fix `check_sql` (CTE names must not satisfy the allowlist; add a function allowlist).
2. **Every protected route returns 401 without credentials.** Parametrize over `app.routes`, so a new unprotected endpoint fails CI.
3. **Auth edge cases:** Entra `verify_jwt` with a locally generated RSA key (the `signing_key` seam), the 403 role path, precedence (bearer, then cookie, then API key), and a non-ASCII `X-API-Key` must give 401 (today `compare_digest` raises `TypeError`, a 500).
4. **Prod guard:** `ENVIRONMENT=prod` with `cookie_secure` off, a short `JWT_SECRET` or empty `AUTH_USERS` fails at startup.
5. **Secret hygiene:** `core/logging.py` redacts `extra={"api_key": ...}` to `***`; error bodies never echo tokens; no `access-control-allow-origin` header (no CORS by design).
6. **SQL injection:** for `run_kpis`, `overdue_work_orders` and `list_assets`, assert that no user-supplied string appears in the SQL text (only bound parameters).

### B. Agent behaviour (fake LLM, fake Databricks)
1. `check_plan`: tool allowlist (`get_kpi`, `query_kpi_table`), malformed arguments, `KpiRequest` validation, `unverified_numbers`.
2. Graph flow: **nothing executes before approval**; `declined` executes nothing; a double approve gives 409; a disallowed tool in the plan loops back to revise and stops at the retry limit; resume after `interrupt()` runs the plan.
3. Out-of-scope question or unavailable data returns the structured answer (`answer`, `evidence`, `filters`, `source_measure`, `limitations`) with no tool call (spec §6.5).
4. `compute_kpi` SQL and parameters, `run_detail_sql` table rewriting and the 200-row cap, `query_with_retries` (3 attempts).
5. Follow-up questions: same `conversation_id` shares history (`memory.py`, in-process and fake Redis; partly covered already).

### C. API contract: concrete HTTP requests
**What "contract" means:** for each endpoint, what you send (method, path, query or JSON body, credentials) and what you must get back (status code and JSON shape, always `{"error": {...}}` for failures). The tests send real HTTP requests to the app **in-process** (FastAPI `TestClient`) with a **fake database returning template rows**, so nothing leaves the machine.

I will write each request as one row in a table in `tests/unit/test_api_contract.py`. Auth for all authenticated rows: `X-API-Key: test-key` in the tests (cookie login is covered in the auth tests).

| # | Request | Expected |
|---|---|---|
| 1 | `GET /health` | 200 `{"environment": "dev"}` |
| 2 | `GET /ready` (fake DB ok / failing) | 200 / 503 |
| 3 | `GET /kpis` without a key | 401, error shape |
| 4 | `GET /kpis` with key | 200, list of `{id, unit, description}` |
| 5 | `POST /auth/login` `{"email","password"}` right / wrong / missing field | 200 plus HttpOnly cookie / 401 / 422 |
| 6 | `GET /auth/me` with and without cookie | 200 `{email,name,role}` / 401 |
| 7 | `POST /analytics/query` `{"kpis":["availability_pct"],"group_by":["site"],"filters":{"month_from":"2025-01"},"limit":10}` | 200 `{kpis, rows, row_count, as_of_ts, ...}` |
| 8 | `POST /analytics/query` with `{"kpis":[]}`, an unknown KPI, `limit: 5000`, an extra field | 422 each, error shape |
| 9 | `GET /fleet/summary` | 200 `{kpis, status_counts, availability_trend}` |
| 10 | `GET /maintenance/summary` | 200 `{kpis, monthly, cost_by_type}` |
| 11 | `GET /assets?status=critical&page=1&page_size=20` | 200 `{items, total, page, page_size}` |
| 12 | `GET /assets?page_size=500`, `?status=bogus` | 422 |
| 13 | `GET /assets/EQ-10002`, `/assets/eq-10002` (case), `/assets/EQ-99999` (unknown), `/assets/abc` | 200 / 200 / 404 / 422 |
| 14 | `GET /assets/EQ-10002/sensors?range=7d`, `?range=2y` | 200 / 422 |
| 15 | `GET /alarms?asset_id=EQ-10002&limit=50` | 200 list |
| 16 | `GET /maintenance/work-orders?status=overdue&limit=20` | 200 list |
| 17 | `GET /equipment/EQ-10002/health` and unknown id | 200 `{equipment, kpis, recent_alarms, open_work_orders}` / 404 |
| 18 | `GET /work-orders/overdue?priority=P1&site_code=PLT-B&page=1` | 200 `{items, page, page_size, total, has_next}` |
| 19 | `POST /assistant/query` `{"question":"What is the MTBF of EQ-10002?"}` (fake agent) | 200 with `run_id` and a plan waiting for approval |
| 20 | `POST /assistant/approve` approve / decline / unknown `run_id` / second approve (body fields confirmed against `routes.py` when writing) | 200 answer / 200 declined / 404 / 409 |
| 21 | Any failing call | `{"error": {...}}` plus `X-Correlation-ID` header; a client-sent ID is echoed |
| 22 | Fake DB raises a connection error | 503 error shape, no stack trace |

### D. Frontend
`npm run build` only (job `frontend`). Optionally later: Vitest on `src/api/client.js` with `VITE_USE_MOCK=true`.

## 3. Not in CI
- `tests/integration/test_assistant_live.py` and the moved `test_kpi_tools.py` (real Azure OpenAI and Databricks, about 40 LLM calls): run by hand when needed.
- `tests/data_quality`: belongs to Databricks jobs.
- Docker build, Bicep, deploy: out of scope (infra and CD dropped). Say so if you want a docker-build job added later.

## 4. Order of work
1. P1–P3 (tiny PR) and `ci.yml` with `test` and `frontend`; green on the existing tests.
2. Block C (it needs the fake DB fixture), then A, then B.
3. Optional: a coverage floor measured from the first run.

## 5. Verification
- Locally: rename `.env`, then `uv sync --locked --extra dev && uv run pytest -m "not integration" tests/unit` is green.
- Push a branch with one deliberately failing test and confirm the PR check goes red, then fix it and confirm green.
- Per CLAUDE.md, copy this plan into `tasks/todo.md` as checkable items once approved.
