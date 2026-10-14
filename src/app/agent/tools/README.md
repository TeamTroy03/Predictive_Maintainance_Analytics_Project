# Agent tools

Spec §6.5: typed tools the assistant may call. The LLM must never bypass the semantic layer, invent
data or change data. The tools are the only code path from the assistant to Gold. They are registered
in the `TOOLS` allowlist in [`../agent.py`](../README.md).

| File | Tool(s) | Status |
|---|---|---|
| `kpi_tools.py` | `get_kpi` | Implemented, queries Gold on Databricks (tested live, see Tests) |
| `graph_tools.py`, `graph_sync.py` | `query_graph` (wired into `agent.py`); `graph_sync` is the CLI that loads the graph into Neo4j | Implemented; knowledge graph over Gold, Neo4j with in-memory fallback (see below) |
| `detail_tools.py` | `query_kpi_table` (wired into `agent.py`); `get_kpi_schema`, `validate_and_format_kpi_query` (atomic variant, **not** wired) | Implemented; queries Gold on Databricks. Guardrail unit-tested (one test is stale, see below) |

## `kpi_tools.py`: `get_kpi`

One governed KPI value for a month range, with optional filters. **The LLM only picks the KPI and the
filters; the number is calculated in Databricks**, never estimated by the model and never written as
SQL by the model.

```
get_kpi(kpi_name, start_month, end_month, equipment_type?, equipment_id?)
   └─► KpiRequest (pydantic)  validates the arguments        ── invalid ─► {"error": "Invalid KPI request: ..."}
   └─► compute_kpi            builds ONE parameterized SELECT on gold_kpi_equipment_month
          └─► run_query       Databricks SQL connector, returns the single row
   └─► dict with value, unit, filters, source_measure, as_of_ts, gold_loaded_ts, row_count, limitations
```

### Arguments

| Argument | Rule |
|---|---|
| `kpi_name` | One of the 12 KPIs below (`Literal` built from `KPIS`) |
| `start_month`, `end_month` | `YYYY-MM`, both inclusive; `end_month` must not be before `start_month` |
| `equipment_type` | optional, letters/digits/space and `_/().-`, up to 64 chars, case-insensitive (matches `dim_equipment_type.equipment_type`) |
| `equipment_id` | optional, `[A-Za-z0-9_-]{1,32}`, case-insensitive (matches `dim_equipment.equipment_id`) |

Unknown extra arguments are rejected (`extra="forbid"`). The same `KpiRequest` is used by
`agent.check_plan`, so a bad plan is caught before it runs.

### The KPIs (`KPIS`)

`KPIS` is the single catalogue: name → `formula`, `unit`, `definition`, `limitations` (and an optional
`note`). The formulas are copied from `KPI_FORMULAS` in the Gold notebook (a copy of it is in
`.claude/reference/gold_layer.ipynb`; the notebook is not in `databricks/`). The additive columns of
`gold_kpi_equipment_month` are summed over the selected rows and then the formula is applied, so a
ratio over several months or equipment is a ratio of sums, not an average of ratios.

| `kpi_name` | Unit | Formula (summary) |
|---|---|---|
| `availability` | ratio (0-1) | (calendar hours − downtime hours) / calendar hours |
| `unplanned_downtime_hours` | hours | sum of `unplanned_downtime_hours` |
| `unplanned_failures` | count | sum of `unplanned_failure_count` |
| `mtbf_hours` | hours | operating uptime hours / unplanned failures |
| `mttr_hours` | hours | unplanned downtime hours / unplanned failures |
| `overdue_work_orders` | count | sum of `overdue_work_order_count` |
| `alarm_rate` | readings per asset-day | alarm count / asset days |
| `alarm_warning_rate` | readings per asset-day | warning count / asset days |
| `alarm_critical_rate` | readings per asset-day | critical count / asset days |
| `maintenance_cost_eur` | EUR | sum of `maintenance_cost_eur` |
| `repeat_failure_rate` | ratio (0-1) | repeat failures / unplanned failures |
| `work_orders` | count | sum of `work_order_count` |

The planner prompt in `agent.py` is generated from `KPIS`, so adding a KPI here makes it available to
the assistant. Keep the formula in sync with the Gold notebook.

### Query construction and safety

- The formula comes only from the `KPIS` constant. User values are only ever **bound parameters**
  (`:start`, `:end`, `:equipment_type`, ...), never concatenated into the SQL.
- Table names come from configuration and must match `[\w.]+` (`_table()` raises otherwise).
- Month filter: `month_start_date_key BETWEEN :start AND :end` (keys are `yyyyMMdd`, first day of month).
- Unknown members (`-1` keys for equipment, site, equipment type) are always excluded.
- Filters (`FILTERS`) are applied as `key IN (SELECT key FROM <dimension> WHERE <condition>)`, comparing
  upper/lower-cased because Gold stores codes and IDs upper-cased.
- The query is a single `SELECT` that returns `value`, `row_count`, `as_of_ts` and `gold_loaded_ts`.

### Result

```python
{
    "kpi_name": "mttr_hours", "value": 4.2, "unit": "hours",
    "filters": {"start_month": "2026-01", "end_month": "2026-03", "equipment_type": "Pump"},
    "source_measure": "<catalog>.<schema>.<prefix>gold_kpi_equipment_month: SUM(...) / NULLIF(...)",
    "as_of_ts": "...", "gold_loaded_ts": "...", "row_count": 37,
    "limitations": ["..."],
}
```

`source_measure`, `as_of_ts` and `gold_loaded_ts` give the evidence and lineage for the answer;
`limitations` always carries the KPI's own caveats (e.g. monthly grain, partial latest month, alarm
readings are not distinct events).

| Situation | Result |
|---|---|
| No Gold rows match the period/filters | `value: None`, `row_count: 0`, limitation to check the months and filter spelling |
| Zero denominator (e.g. no failures, so MTBF/MTTR/repeat rate are undefined) | `value: None` (never 0 or infinity), limitation explaining it |
| Invalid arguments | `{"error": "Invalid KPI request: ..."}`, no query is sent |
| Databricks/connection error | exception from `run_query`; `agent.execute()` catches it and reports it in `limitations` |

`mtbf_hours` carries an **open decision** note: it uses `operating_uptime_hours` (scaled by
`operating_share`), not calendar uptime.

### Configuration

Environment variables or `.env` (never in code; Key Vault in the deployed solution). The Databricks
connector is imported inside `run_query`, so the module can be imported without it installed.

| Variable | Meaning |
|---|---|
| `DATABRICKS_HOST` | workspace / SQL warehouse hostname |
| `DATABRICKS_HTTP_PATH` | SQL warehouse HTTP path |
| `DATABRICKS_TOKEN` | access token, give its identity `SELECT` only on the Gold tables (§6.6) |
| `GOLD_CATALOG`, `GOLD_SCHEMA` | where the Gold tables live |
| `GOLD_TABLE_PREFIX` | optional, defaults to `test_` (tables are `<prefix>gold_<name>`, e.g. `test_gold_dim_site`) |

### Tests

`.tests/test_kpi_tools.py` runs against the **real Gold tables** (marked `integration`, skipped when the
variables above are missing):

```bash
pytest src/app/agent/tools/.tests -v
```

No numbers are hard-coded because Gold is rebuilt on every pipeline run. The tests check invariants:
all 12 KPIs run (whole fleet and with all filters), ratios are within 0-1, other KPIs are not negative,
warning + critical = alarm rate, MTTR = hours / failures, zero-denominator cases give `None` with a
limitation, additive KPIs over a range equal the sum of the months, filters only narrow the result,
equipment ID/type filters ignore case, empty periods and unknown equipment return `None` with a limitation, and bad
requests (end before start, invalid month, SQL injection attempt in a filter) return an error without
running a query.

The tests live in a dot-folder under `src/`, so run them by path as shown. Not covered yet: an offline unit test of `compute_kpi` with a stub `run` (the function accepts one, e.g.
`compute_kpi(request, run=fake)`), so CI without Databricks could check the generated SQL and params.

## `detail_tools.py`: `query_kpi_table` (guarded SQL, wired into `agent.py`)

For "show me" questions that `get_kpi` cannot answer (lists, breakdowns per month, rankings with equipment,
type and site names). The tool takes only a plain-language `question`; **an LLM writes the SQL, but code checks
it before anything runs**:

```
query_kpi_table(question)
   └─► query_with_retries   (at most MAX_ATTEMPTS = 3 tries)
         1. describe(get_schema())   RULES + per table: columns, types, comments, SAMPLE_ROWS = 3 sample rows
         2. write_sql(question, schema, failed tries)   LLM step, built by make_sql_writer(llm) from WRITER_PROMPT
         3. clean_sql      drops ``` fences
         4. check_sql      sqlglot parse: ONE statement, a SELECT only (no INSERT/UPDATE/DELETE/MERGE,
                           CREATE/DROP/ALTER, SELECT ... INTO, stacked statements, other commands, also inside
                           subqueries/CTEs) and only the tables of TABLES
         5. limit_rows     keeps a LIMIT <= 200 (MAX_ROWS), replaces a bigger or missing one
         6. run_detail_sql maps the table names to the real Gold tables and runs it on Databricks
         a rejection or a Databricks error goes back to step 2 as "Previous attempt ... Rejected: ... Fix it."
   └─► {"sql", "rows", "row_count", "attempts"}  or  {"error": "No valid query after 3 attempts.", "attempts": [...]}
```

- **Allowed tables (`TABLES`)**: `kpi_table` (= `gold_kpi_equipment_month`), `dim_equipment`,
  `dim_equipment_type`, `dim_site`. Any other table is rejected by `check_sql`. Unknown members (`-1` keys) are
  excluded by the writer prompt and the rules text.
- `get_schema()` reads each table once per process (`@cache`: restart to see new columns) with
  `DESCRIBE TABLE` and a `SELECT ... LIMIT 3` sample. `run_rows` returns every row as a dict (Decimal → float).
- The LLM is never created in this file: the caller passes it to `make_sql_writer` (`agent.py` does), or passes
  its own `write_sql`, which is how the unit tests run without an LLM.
- The parser check is not the only protection: the token should have `SELECT` rights only on the Gold tables.
- The agent shows the SQL that ran in `source_measure`, and `agent.execute` checks the numbers in the answer
  against the returned rows.

### The atomic variant (`make_kpi_tools`, not wired)

`make_kpi_tools(get_schema)` returns `get_kpi_schema` (the `describe()` text of all tables) and
`validate_and_format_kpi_query(sql_query)` (`clean_sql` + `check_sql` + `limit_rows`, returns
`{"valid": True, "sql": ...}` or `{"error": "Guardrail rejected query: ..."}`; it runs nothing). They are the same
steps without the retry loop, for an LLM that loops by itself. **`agent.py` does not use them**; its `TOOLS`
are `get_kpi` and `query_kpi_table`.

### Tests

`tests/unit/test_detail_tools.py` covers `check_sql` (rejected writes, accepted SELECT), `limit_rows` and the
atomic tools, with no Databricks and no LLM. **`test_get_kpi_schema` currently fails**: it passes the old
single-table shape (`{"columns": [...], "sample": [...]}`) while `get_schema()` now returns
`{table name: {"columns", "sample"}}`. Not covered yet: `query_with_retries` with a fake `write_sql`/`run_sql`,
the table allowlist in `check_sql`, and `run_detail_sql`'s table renaming. The live path is exercised by
`tests/integration/test_assistant_live.py` only when the planner picks this tool.

## `graph_tools.py`: `query_graph` (knowledge graph, Neo4j with in-memory fallback)

Relationship questions that the KPI star schema answers badly: which failure modes an asset or an equipment type has,
repeat-failure chains, what tends to fail after what. **The LLM only picks a template and its arguments; it never writes
Cypher or SQL**, and the numbers are the counts and sums stored on the Gold graph edges (periods and KPIs stay with `get_kpi`).

```
Gold dims + facts --(built in Databricks, not by this repo)--> gold_kg_nodes, gold_kg_edges   (Delta, source of truth)
gold_kg_* --(graph_sync.py)--> Neo4j        query_graph --> GraphService --> Neo4jStore  (fixed Cypher per template, read-only session)
gold_kg_* --(load_tables)--> in-process NetworkX graph                  \--> MemoryStore (same templates, same result shape)
```

### Templates (`TEMPLATES`) and arguments

| `template` | Needs | Returns (rows) |
|---|---|---|
| `equipment_failure_modes` | `equipment_id` | per failure mode: `failures`, `downtime_hours`, `repeat_failures`, `last_failure_ts` |
| `type_failure_profile` | `equipment_type` | per failure mode: `failures`, `equipment_affected`, `downtime_hours` |
| `repeat_failure_chain` | optional `equipment_id` (none = whole fleet) | each repeat failure, newest first: `work_order`, `equipment_id`, `repeats_work_order`, `failure_mode`, `gap_days`, `created_ts` |
| `failures_after` | `failure_mode`, optional `equipment_type` | per next failure mode: `occurrences`, `avg_gap_days` |

`limit` is 1-50 (default 20). `GraphRequest` (pydantic, `extra="forbid"`) rejects missing or unused arguments for the template and
characters outside the allowed patterns; `agent.check_plan` applies the same model (`REQUESTS`), so a bad plan is caught before it
runs. Names are matched case-insensitively against the node's business key or label, so `failure_mode` can be `BRG-WEAR` or
`Bearing wear`. A name that is not in the graph gives no rows and a limitation, not an error.

### The graph tables

Already built in Databricks from the Gold tables; nothing in this repo creates them (columns and rules in
`gold_layer_data_dictionary.md`, section 8): nodes `Site, EquipmentType, Equipment, Sensor, FailureMode, MaintenanceType, WorkOrder` (`node_id = <Type>:<business key>`),
edges `LOCATED_AT, IS_OF_TYPE, HAS_SENSOR, PERFORMED_ON, HAS_FAILURE_MODE, HAS_MAINTENANCE_TYPE` and the derived
`REPEATED_FAILURE` (from Gold's `is_repeat_failure`), `FOLLOWED_BY`, `EXPERIENCED_FAILURE`, `TYPE_PRONE_TO`. Unknown members and
failure mode `NONE` are left out. Table names are `<prefix>gold_kg_nodes` / `<prefix>gold_kg_edges`; the prefix is
`GRAPH_TABLE_PREFIX` when set, else `GOLD_TABLE_PREFIX` (so the graph tables can be `test_` while the KPI tables are not).

### Backends and fallback

| | |
|---|---|
| Neo4j | Used when `NEO4J_URI` is set. One fixed, parameterised Cypher query per template in `CYPHER`; values are only bound parameters; one read session per query with a server-side timeout (`NEO4J_TIMEOUT_SECONDS`, default 5). |
| Memory | Otherwise, or when Neo4j fails: a NetworkX graph built from the same two Gold tables on first use and reused for `GRAPH_CACHE_SECONDS` (default 3600). Needs Databricks, not Neo4j. |

The driver connects lazily. A Neo4j error (down, bad credentials, empty database) is logged as `graph_neo4j_unavailable`, the question
is answered from memory with a limitation saying so, and Neo4j is not tried again for 60 s. Every result has `backend`
(`neo4j` or `memory`). If Databricks is down as well, the tool raises and `execute` reports the failure in `limitations`.
Neo4j only has the data of the last sync; the result shows the `gold_loaded_ts` it was built from. The API syncs by itself at startup (below), so after a new Gold run restart the API or run `graph_sync` by hand.

### Sync at API startup

When the API starts (`create_app` lifespan in `app/api/main.py`, so also in the Docker image that serves the site) and `NEO4J_URI` is set, `start_background_sync()` runs `startup_sync()` in a daemon thread. It compares the `gold_loaded_ts` of the Gold graph tables with the one stored in Neo4j (`GraphMeta`) and loads Neo4j **only when Gold is newer or Neo4j is empty**; otherwise a restart costs two small queries. It never blocks or fails the start: until the first sync has finished (about 35 s for 20k nodes / 49k edges) `query_graph` answers from the in-memory graph, and an error is only logged (`graph_sync_started`, `graph_sync_finished` with the result, `graph_sync_failed` with the error type). `GRAPH_SYNC_ON_STARTUP=false` switches it off. Run one API worker, like the rest of the assistant.

### Loading Neo4j by hand (`graph_sync.py`)

```bash
docker run -d --name pm-neo4j -p 127.0.0.1:7687:7687 -e NEO4J_AUTH=neo4j/<password> neo4j:5
# .env
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<password>
uv run python -m app.agent.tools.graph_sync --dry-run   # reads the Gold tables, prints the counts, no Neo4j
uv run python -m app.agent.tools.graph_sync             # loads them (about a minute for 20k nodes / 49k edges)
```

Idempotent: `MERGE` by `node_id` and by (source, target, type), everything of a run carries one `sync_id`, and what is no longer in
Gold is deleted at the end, so reruns do not duplicate. Each node has the labels `Node` and its type; relationship type = `edge_type`;
the JSON `properties` become plain properties. A `(:GraphMeta {id: 'kg'})` node keeps the `gold_loaded_ts` of the copy. A node or edge
type that is not in `NODE_TYPES` / `EDGE_TYPES` of `graph_sync.py` stops the sync before any write (labels cannot be parameters).
The sync replaces everything in the target database: use a database of its own. Credentials come from `.env` locally and Key Vault when deployed.

### Tests

| File | What it covers |
|---|---|
| `tests/unit/test_graph_tools.py` | request validation, every template on a fixture graph (memory), Neo4j store with a fake runner (parameters bound, empty graph), fallback and cooldown, the tool, the sync with a fake driver (batches, order, labels, unknown types) |
| `tests/integration/test_graph_neo4j.py` | contract test: the same fixture in a real throwaway Neo4j (`NEO4J_TEST_URI`), both backends must return identical rows, filters and limitations for every template; sync idempotent |

Commands:

```bash
uv run python -m app.agent.tools.graph_sync --dry-run   # reads gold_kg_nodes / gold_kg_edges and prints the counts, no Neo4j
uv run python -m app.agent.tools.graph_sync             # manual refresh of Neo4j (the API also does it when it starts, if Gold is newer)
uv run pytest tests/unit/test_graph_tools.py            # unit tests of query_graph and the sync (no Neo4j, Databricks or LLM)
docker exec pm-neo4j cypher-shell -u neo4j -p <password> "MATCH (n:Node) RETURN n.node_type, count(*) ORDER BY 1"   # look inside
```

Contract test of the two backends (needs a **throwaway** Neo4j, the test replaces everything in its database; it is skipped without `NEO4J_TEST_URI`):

```bash
docker run -d --name pm-neo4j-test -p 127.0.0.1:7687:7687 -e NEO4J_AUTH=neo4j/<test-password> neo4j:5
# wait about 20 s, then (PowerShell: $env:NEO4J_TEST_URI="bolt://127.0.0.1:7687"; $env:NEO4J_TEST_PASSWORD="<test-password>")
NEO4J_TEST_URI=bolt://127.0.0.1:7687 NEO4J_TEST_PASSWORD=<test-password> uv run pytest tests/integration/test_graph_neo4j.py -m integration -v
docker rm -f pm-neo4j-test
```
