"""KPI table tools for the Planner -> Judge -> Executor graph, split into two atomic steps: `get_kpi_schema` describes
the table, `execute_kpi_query` runs SQL the Executor wrote from that description after a parser guardrail accepted only
a single read-only SELECT capped at 200 rows. This file has no Databricks code: kpi_tools passes in `get_schema` and
`run_sql` (contract in tools/README.md)."""

import sqlglot
from langchain_core.tools import tool
from sqlglot import exp

MAX_ROWS = 200

# Anything that writes data or changes tables (DML/DDL/Command cover insert, update, delete, merge, create, drop, ...)
_FORBIDDEN = (exp.DML, exp.DDL, exp.Command, exp.Into)


def check_sql(sql: str) -> str | None:
    """None if `sql` is a single read-only SELECT, else the reason it is rejected."""
    try:
        statements = sqlglot.parse(sql, read="databricks")
    except sqlglot.errors.SqlglotError as e:
        return f"invalid SQL: {str(e).splitlines()[0]}"  # first line only, sqlglot errors are long
    # parse() returns one tree per ";"-separated statement, so stacked statements fail the length check
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        return "only a single SELECT query is allowed"
    # find() searches the whole tree, so a write hidden in a subquery/CTE is caught too
    if bad := statements[0].find(*_FORBIDDEN):
        return f"{type(bad).__name__.upper()} is not allowed, queries must be read-only"
    return None


def limit_rows(sql: str) -> str:
    """Cap a checked query at MAX_ROWS rows; a smaller LIMIT written by the LLM is kept."""
    query = sqlglot.parse_one(sql, read="databricks")
    limit = query.args.get("limit")
    n = limit.expression if limit else None  # the number in "LIMIT n"
    # keep only a plain integer <= MAX_ROWS; a missing, larger or non-literal limit is replaced
    if not (isinstance(n, exp.Literal) and n.is_int and int(n.name) <= MAX_ROWS):
        query = query.limit(MAX_ROWS)
    return query.sql(dialect="databricks")


def make_kpi_tools(get_schema):
    """Build [get_kpi_schema, validate_and_format_kpi_query]. get_schema() -> {"columns": [{"name", "type", "comment"?}],
    "sample"?: [row dict]}; -> list of row dicts."""

    @tool
    def get_kpi_schema() -> str:
        """Retrieve schema metadata and sample rows for `kpi_table`.
        Returns column names, data types, descriptions, and example data.
        Call this whenever you need to inspect available columns before constructing
        a SQL query for `execute_kpi_query`.
        """
        schema = get_schema()
        # one line per column: "- name (type): comment" (comment is optional)
        cols = "\n".join(
            f"- {c['name']} ({c['type']})" + (f": {c['comment']}" if c.get("comment") else "")
            for c in schema["columns"]
        )
        rows = "\n".join(str(r) for r in schema.get("sample", []))  # sample rows are optional
        return f"Table kpi_table:\n{cols}" + (f"\nSample rows:\n{rows}" if rows else "")

    @tool
    def validate_and_format_kpi_query(sql_query: str) -> dict:
        """Runs an AST safety guardrail to ensure only a single read-only SELECT
        statement is executed. Automatically enforces a limit of at most 200 rows.
        """
        cleaned_sql = sql_query.strip().strip("`").removeprefix("sql").strip()  # drop ```sql fences the LLM may add

        error = check_sql(cleaned_sql)
        if error:
            return {"error": f"Guardrail rejected query: {error}"}

        safe_sql = limit_rows(cleaned_sql)
        return {"valid": True, "sql": safe_sql}  # checked and limited SQL; nothing is run here

    return [get_kpi_schema, validate_and_format_kpi_query]
