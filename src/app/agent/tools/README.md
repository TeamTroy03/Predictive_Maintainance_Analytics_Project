# Agent tools: how `detail_tools.py` works and what `kpi_tools.py` must provide

Spec §6.5: typed tools the assistant may call; the LLM must never bypass the semantic layer, invent data or change data.

## How `detail_tools.py` works (done)

Two atomic tools from `make_kpi_tools(get_schema)`, so the Planner can select them, the Judge can review the calls, and
the Executor writes the SQL itself in between (no LLM inside the tools).

```
get_kpi_schema()             ──► get_schema() formatted as text: columns, types, comments, sample rows
   (Executor LLM writes a Databricks SQL SELECT from that text)
validate_and_format_kpi_query(sql_query) ──► strips ``` fences
                             ──► check_sql    sqlglot parser: ONE statement, a SELECT only; rejects INSERT/UPDATE/DELETE/MERGE,
                                              CREATE/DROP/ALTER, SELECT ... INTO, stacked statements, SHOW and other commands
                                 limit_rows   adds LIMIT 200 (keeps a smaller LIMIT, replaces a bigger or missing one)
                             ──► {"valid": True, "sql": "..."}   (or {"error": "Guardrail rejected query: ..."})
```

`validate_and_format_kpi_query` does **not** run anything: it only returns the checked, limited SQL. Sending it to
Databricks is the job of whoever calls the tool next (see `run_sql` below). `detail_tools.py` has no Databricks code; it
receives only `get_schema` from `kpi_tools.py`.

## What we need from `kpi_tools.py`

Everything that talks to Azure Databricks lives in `kpi_tools.py` (SDK `databricks-sdk`, token and host from
environment variables / Key Vault, never in code).

### 1. `get_schema() -> dict`

Return the table the assistant may query, called as `kpi_table`:

```python
{
    "columns": [{"name": "alarm_rate", "type": "double", "comment": "share of readings above alarm limit"}, ...],
    "sample": [{"alarm_rate": "0.02", "site": "Plant A"}, ...],   # optional, 3-5 rows
}
```

- `comment` is optional but makes the generated SQL better (Unity Catalog column comments).
- Suggested source: `w.tables.get("catalog.schema.table")` for `.columns[i].name / .type_text / .comment`, and
  `execute_statement(... "SELECT * FROM catalog.schema.table LIMIT 5")` for the sample.
- Cache it (it rarely changes); do not call Databricks on every question.
- The LLM will write `FROM kpi_table`, so `run_sql` must make that name resolve (default catalog/schema plus a
  `kpi_table` view, or rewrite the name to `catalog.schema.table`).

### 2. `run_sql(sql: str) -> list[dict]`

Not used by `detail_tools.py` any more. Whoever executes the SQL returned by `validate_and_format_kpi_query` needs a
function like this: run one SQL string against Databricks and return rows as dicts keyed by column name:

```python
r = w.statement_execution.execute_statement(warehouse_id=..., statement=sql, wait_timeout="30s", row_limit=200)
names = [c.name for c in r.manifest.schema.columns]
return [dict(zip(names, row)) for row in (r.result.data_array or [])]
```

Notes on the SDK format:
- `data_array` is a list of lists of **strings** (numbers and timestamps too, `NULL` is `None`). Converting to real
  types with `manifest.schema.columns[i].type_name` is optional.
- Check `r.status.state`; raise on `FAILED` / timeout. `agent.execute()` reports exceptions as limitations.
- Large results arrive in chunks (`next_chunk_index`); with `row_limit=200` one chunk is enough.

**Required safety:** since nothing in `detail_tools` guarantees the SQL passed through the validator, start `run_sql`
with

```python
from app.agent.tools.detail_tools import check_sql, limit_rows
if reason := check_sql(sql):
    raise ValueError(reason)
sql = limit_rows(sql)
```

so no code path can send unchecked SQL to Databricks. Also pass `row_limit=200` and give the token's identity
`SELECT` only on the Gold table (least privilege, spec §6.6).

### 3. Wire into `agent.py`

```python
from app.agent.tools.detail_tools import make_kpi_tools
get_kpi_schema, validate_and_format_kpi_query = make_kpi_tools(get_schema)
TOOLS = {t.name: t for t in (get_kpi, get_kpi_schema, validate_and_format_kpi_query, ...)}
```

## Tests

`tests/unit/test_detail_tools.py` mocks `get_schema` and covers `check_sql`, `limit_rows` and both tools. For
`kpi_tools` please add a test with a mocked `WorkspaceClient` that checks the row mapping and that unsafe SQL raises
before any call is made.
