# Team 3 — Predictive Maintenance Analytics

AI and Data Capstone (Accenture / Code.Hub). Spec: `[context/](context/)`.

An industrial company needs to understand equipment health, abnormal sensor behavior,
maintenance effectiveness, downtime and recurring failure modes.

## Solution path

`src/data/` (CSV + JSON) → Databricks Bronze → Silver → Gold (Delta star schema)
→ Microsoft Fabric semantic model → Power BI report,
with FastAPI + LangChain Core exposing approved analytical tools, hosted on Azure and delivered via GitHub Actions.

## Spec → repository map


| Spec | Scope                          | Code                                        | Docs                                      |
| ---- | ------------------------------ | ------------------------------------------- | ----------------------------------------- |
| §4   | Dataset                        | `src/data/`                                 | `docs/business_brief.md`                  |
| §6.1 | Bronze ingestion               | `databricks/bronze/`, `databricks/common/`  | `docs/databricks_implementation.md`       |
| §6.2 | Silver preparation             | `databricks/silver/`, `tests/data_quality/` | `docs/quality_evidence.md`                |
| §6.3 | Gold dimensional model         | `databricks/golden/`                        | `docs/dimensional_model.md`               |
| §6.4 | Fabric semantic model & report | `fabric/`                                   | `docs/fabric_powerbi.md`                  |
| §6.5 | LangChain Core                 | `src/app/agent/`                            | `docs/langchain_integration.md`           |
| §6.6 | Azure fundamentals             | `infra/`                                    | `docs/azure_design.md`                    |
| §6.7 | REST / FastAPI                 | `src/app/api/`, `postman/`                  | `docs/fastapi_integration.md`             |
| §6.8 | CI/CD                          | `.github/workflows/`, `Dockerfile`          | `docs/cicd.md`                            |
| §7   | Testing & operations           | `tests/`                                    | `docs/runbook.md`                         |
| §8   | Project management             | —                                           | `docs/project_management.md`, `docs/adr/` |




## What each task in §6 asks for

§6 of the spec is the mandatory scope of work. All eight tasks apply to every team; the "Team 3" notes are our case.
The weight is the share of the final grade (§9; §6.6 and §6.7 are graded together). Optional extras only count after the
whole baseline works end to end.


| §   | Task                                            | What it asks for                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | Done when (acceptance)                                                                                                                                                      | Weight          |
| --- | ----------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------- |
| 6.1 | **Databricks: Bronze ingestion**                | Load the raw CSV and JSON files into Delta tables exactly as delivered, with no business changes, and record where every row came from. Needs: parameterized ingestion for CSV and JSON; ingestion timestamp, batch ID, source file name, record hash and load status on every row; schema validation with a controlled reaction to schema drift; reruns that do not create duplicates (incremental load or a justified full refresh); a log of input, written and rejected counts, duration and failures.                                                                                                                                                                                                                                                                                                 | Any downstream record can be traced to its source file and batch, and a failed batch can be rerun without uncontrolled duplicates.                                          | 12 %            |
| 6.2 | **Databricks: Silver preparation**              | Turn the raw data into clean, consistent, analysis-ready tables and keep the rejected rows visible. Needs: standardized names, types, dates, time zones, codes, currencies and units; domain rules, deduplication and a decision for invalid or missing keys; flattened JSON joined with the reference data; data-quality checks for completeness, validity, uniqueness and referential integrity; a quarantine table with reason, rule and timestamp; documented handling of slowly changing reference attributes and late-arriving events. Team 3 priority rules: out-of-range readings, unit inconsistency, missing equipment keys, overlapping downtime, work order closed before created, duplicate sensor events.                                                                                    | Records are analytically consistent, quality results are measurable, and rejected records stay visible and actionable.                                                      | 15 %            |
| 6.3 | **Databricks: Gold dimensional model**          | Build a Delta star schema from which every business number can be calculated. Needs: the stated grain of every fact; at least one central fact and four dimensions including a date dimension; surrogate keys and documented handling of unknown members; business measures, status logic and aggregates; an optimized Delta layout with a short performance rationale; a Silver to Gold reconciliation of row counts and critical totals; table and column descriptions. Team 3: facts `fact_sensor_reading`, `fact_maintenance_work_order`, `fact_downtime`; KPIs asset availability, unplanned downtime, MTBF, MTTR, overdue work orders, alarm rate, maintenance cost, repeat-failure rate.                                                                                                            | Business measures can be reproduced from the documented facts and dimensions, with no inconsistent logic hidden in report visuals.                                          | 15 %            |
| 6.4 | **Microsoft Fabric: semantic model and report** | Put a governed business layer and a report on top of Gold, connected the way the instructor agreed. Needs: relationships, hierarchies, formats, descriptions and a dedicated measures table; at least eight explicit DAX measures (volume, rate or ratio, trend, exception); a proper date table with checked filter direction; row-level security if the case has ownership boundaries (Team 3: by site); at least three report pages (executive overview, analytical detail, exceptions or drill-through) with filters, tooltips, accessible titles, clear units and a visible last-refresh indicator; report totals validated against Gold and performance observations written down. Team 3 pages: fleet health, sensor trends, maintenance performance, equipment drill-through, critical exceptions. | Report totals match Gold, and the business terms and logic live in the semantic model (the spec states no separate acceptance sentence for this task).                      | 15 %            |
| 6.5 | **LangChain Core: LLM and tools**               | A small, auditable assistant built with `langchain-core`. The LLM is only an interface to approved analytical functions: it must not bypass the semantic model, invent data or take business actions. Needs: correct system, human, AI and tool messages; prompt templates for business context, evidence, response format and limitations; at least two typed tools (one KPI or aggregate, one filtered detail or metadata) bound to the chat model; detection of tool calls, argument validation, execution and a `ToolMessage` back to the model; structured output with answer, evidence, filters, source or measure and limitations; handling of malformed arguments, unavailable data, tool errors and out-of-scope requests.                                                                        | A question triggers a validated tool call, gets a result grounded in Gold, and produces an evidence-based answer with a traceable execution path.                           | 10 %            |
| 6.6 | **Azure fundamentals**                          | Document and apply the Azure foundation that hosts and secures the components. Needs: the account, subscription and resource group boundary; least-privilege role-based access with the responsibilities of users, developers and the runtime identity; justified Storage, Compute and Networking choices; secrets outside source control, in Key Vault for the deployed solution; an environment diagram and a short resource inventory with naming and responsibilities.                                                                                                                                                                                                                                                                                                                                 | The foundation is documented and applied (diagram, inventory, access model, secrets in Key Vault).                                                                          | 10 % (with 6.7) |
| 6.7 | **REST APIs, FastAPI and Azure integration**    | A documented API that connects consumers to the approved analytics and the assistant. Needs: REST fundamentals (resources, verbs, status codes, consistent errors); FastAPI routes with Pydantic models, validation and exception handling; OpenAPI docs and a Postman collection; timeouts, pagination, bounded retries and rate-limit handling for external calls; endpoint protection (API keys in development, OAuth2 bearer or JWT for the deployed design); health and readiness endpoints, correlation IDs and structured logs without secrets; an end-to-end connection to Gold and the LangChain tools (mocks only when clearly documented). Minimum API: `GET /health`, `POST /analytics/query`, `POST /assistant/query` and one domain endpoint.                                                | The API works end to end against Gold and the assistant, is protected, and is documented and testable (the spec states no separate acceptance sentence for this task).      | 10 % (with 6.6) |
| 6.8 | **Applied DevOps: CI/CD**                       | A versioned pipeline in GitHub Actions or Azure DevOps. Needs: triggers on pull requests and the main branch; separate lint, test, build and deploy jobs with clear dependencies; the API (and its container image) built as a reproducible artifact; secrets only from the repository or pipeline secret store; environments, approvals and staged deployment; documented rollback, configuration promotion and the evidence required before approval.                                                                                                                                                                                                                                                                                                                                                    | A source change triggers the pipeline, the quality gates run automatically, an artifact is built, and the deployment either completes or waits for the configured approval. | 8 %             |


The other sections referenced in this repo: §4 dataset (what the data must contain, see `src/data/`), §5 architecture
(Bronze, Silver, Gold, Fabric, API, assistant, Azure), §7 deliverables (the `docs/` files in the map above), §8 roles and
process (see the spec) and §9 evaluation (the weights above, plus 8 % for business framing and KPIs, 7 % for the demo and
teamwork, and a 5 % bonus for a small knowledge graph exposed through the assistant).

## Repository layout

```text
.github/             CODEOWNERS (git-ignored) · workflows/ci.yml and deploy.yml are planned, not written yet
context/             original spec PDF · silver_max/ (maintenance Silver rules)
databricks/          common/ · bronze/ · silver/ · golden/ · jobs/  (not a Python package)
                     bronze/ and silver/ hold notebooks/scripts; common/ and jobs/ are still empty
docs/                adr/ (0001: new data continues the delivered plant, 0002: knowledge graph); the other deliverable .md files
                     in the map above are still to be written
fabric/              semantic-model/ (model diagrams, PNG) · report/  (PBIP / TMDL source still to come)
infra/bicep-scripts/ main.bicep (skeleton: params only) + dev/prod .bicepparam
postman/             team3-pm-api.postman_collection.json
src/app/
  api/               FastAPI: main.py · routes.py (all endpoints) · schemas.py (Pydantic) · services.py, kpis.py,
                     dashboard.py, assets.py (Gold queries and page payloads) · db.py · security.py · errors.py
  agent/             LangChain Core + LangGraph assistant: agent.py · memory.py · schemas.py · prompts/ · tools/ (get_kpi, query_kpi_table, query_graph, graph_sync)
  core/              config.py · logging.py · security.py (settings, JSON logs, password/session/JWT logic)
  frontend/          React + Vite web app (AssetPulse), talks to the API through /api
  services/          docstring-only stubs, not used: the service layer is src/app/api/services.py
src/data/            generate/ (seeded generator, --continue, base_state.py, checks/silver_check.py) ·
                     upload/ (volume upload with a conflict check) · raw/ (delivered files: *_2 baseline + chain.json)
tests/               unit/ · data_quality/ (no tests yet) · integration/ (live, manual)
tasks/               todo.md (working notes)
```

## Local development

Uses [uv](https://docs.astral.sh/uv/getting-started/installation/) (not pip) and Node.js for the frontend. uv reads the
Python version from `.python-version` (3.14, downloaded if missing) and the exact dependency versions from `uv.lock`.

```bash
uv sync --extra dev                                 # creates .venv and installs the locked dependencies
                                                    # add --extra databricks to upload generated data
#cp .env.example .env                                # fill in locally; never commit .env
uv run ruff check . && uv run pytest -m "not integration"   # what CI runs; integration tests need a deployed environment
uv run uvicorn app.api.main:app --reload            # http://localhost:8000/docs
cd src/app/frontend && npm install && npm run dev   # http://localhost:5173 (proxies /api to the API)
```

`uv run` uses the project's `.venv` automatically, so there is nothing to activate. When you add a dependency, use
`uv add <package>` (or `uv add --optional dev <package>`) so that `pyproject.toml` and `uv.lock` change together.

### Optional services: Redis and Neo4j

The assistant works without them. Redis keeps the conversation memory (follow-up questions) across API restarts. Neo4j holds the knowledge graph (`gold_kg_nodes`, `gold_kg_edges`, already built in Databricks) that the `query_graph` tool queries; without it the tool builds the same graph in memory from the Gold tables. **Order:** containers up and answering, then the API (it copies the graph into Neo4j in a background thread when it starts, only if Gold has a newer load), then the frontend.

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

```bash
uv run uvicorn app.api.main:app --reload                # 2nd terminal: look for graph_sync_finished in the log (about 35 s the first time, then 'up to date')
cd src/app/frontend && npm install && npm run dev       # 3rd terminal: http://localhost:5173
```

Knowledge graph commands:

```bash
uv run python -m app.agent.tools.graph_sync --dry-run   # reads gold_kg_nodes / gold_kg_edges and prints the counts, no Neo4j
uv run python -m app.agent.tools.graph_sync             # manual refresh of Neo4j (the API also does it when it starts, if Gold is newer)
uv run pytest tests/unit/test_graph_tools.py            # unit tests of query_graph and the sync (no Neo4j, Databricks or LLM)
docker exec pm-neo4j cypher-shell -u neo4j -p <password> "MATCH (n:Node) RETURN n.node_type, count(*) ORDER BY 1"   # look inside
```

Run one API worker (pending plans live in memory). Details: [`src/app/agent/README.md`](src/app/agent/README.md) and [`src/app/agent/tools/README.md`](src/app/agent/tools/README.md).

Folder READMEs: `[src/data/](src/data/README.md)` (data, generator, upload), `[src/app/api/](src/app/api/README.md)`,
`[src/app/agent/](src/app/agent/README.md)`, `[src/app/frontend/](src/app/frontend/README.md)`,
`[databricks/](databricks/README.md)`, `[tests/](tests/README.md)`.