# Agent tools: how `detail_tools.py` works and what `kpi_tools.py` must provide

Spec §6.5: typed tools the assistant may call; the LLM must never bypass the semantic layer, invent data or change data.

## How `detail_tools.py` works (done)

One tool for the planner, `query_kpi_table(question)`. The plan in `agent.py` is executed step by step with fixed
arguments, so one step cannot feed another; that is why describe, generate, check and run live inside one tool.

```
question ──► 1. describe_table(get_schema())   text: columns, types, comments, sample rows
         ──► 2. LLM writes a Databricks SQL SELECT from that text (one retry with the rejection reason)
         ──► 3. check_sql    sqlglot parser: ONE statement, a SELECT only; rejects INSERT/UPDATE/DELETE/MERGE,
                             CREATE/DROP/ALTER, SELECT ... INTO, stacked statements, SHOW and other commands
             limit_rows      adds LIMIT 200 (keeps a smaller LIMIT, replaces a bigger or missing one)
         ──► 4. run_sql(sql) ──► {"sql": "...", "rows": [ {col: value}, ... ]}   (or {"error": "..."})
```

Nothing is run unless steps 3 passes. `detail_tools.py` has no Databricks code; it receives two functions from
`kpi_tools.py`.

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

### 2. `run_sql(sql: str) -> list[dict]`

Run one SQL string against Databricks and return rows as dicts keyed by column name:

```python
r = w.statement_execution.execute_statement(warehouse_id=..., statement=sql, wait_timeout="30s", row_limit=200)
names = [c.name for c in r.manifest.schema.columns]
return [dict(zip(names, row)) for row in (r.result.data_array or [])]
```

Notes on the SDK format:
- `data_array` is a list of lists of **strings** (numbers and timestamps too, `NULL` is `None`). Converting to real
  types with `manifest.schema.columns[i].type_name` is optional.
- Check `r.status.state`; raise on `FAILED` / timeout. `detail_tools` lets exceptions propagate and `agent.execute()`
  reports them as limitations.
- Large results arrive in chunks (`next_chunk_index`); with `row_limit=200` one chunk is enough.

**Required safety, even though `detail_tools` already checked:** start `run_sql` with

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
from app.agent.tools.detail_tools import make_query_kpi_table
query_kpi_table = make_query_kpi_table(llm, get_schema, run_sql)
TOOLS = {t.name: t for t in (get_kpi, query_kpi_table, ...)}
```

`llm` is defined in `agent.py`, so the tool is built there (importing `agent.py` from the tool would be circular).

## Tests

`tests/unit/test_detail_tools.py` mocks the LLM, `get_schema` and `run_sql`. For `kpi_tools` please add a test with a
mocked `WorkspaceClient` that checks the row mapping and that unsafe SQL raises before any call is made.