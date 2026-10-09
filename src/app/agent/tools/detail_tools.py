"""query_kpi_table: answers a detail question about the KPI table in four steps: describe the table, the LLM
writes SQL from that description, a parser guardrail accepts only a single read-only SELECT capped at 200 rows,
then the SQL is run. This file has no Databricks code: kpi_tools passes in `get_schema` and `run_sql`
(contract in tools/README.md)."""

import sqlglot
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool
from sqlglot import exp

MAX_ROWS = 200

# Anything that writes data or changes tables (DML/DDL/Command cover insert, update, delete, merge, create, drop, ...)
_FORBIDDEN = (exp.DML, exp.DDL, exp.Command, exp.Into)
_SQL_PROMPT = (
    "Write ONE read-only Databricks SQL SELECT over the table kpi_table. Description of the table:\n{description}\n"
    "Use only these columns. Return only the SQL, no explanation."
)


def describe_table(schema: dict) -> str:
    """Step 1: plain text for the LLM from kpi_tools' schema: {"columns": [{"name", "type", "comment"?}], "sample"?: [row dict]}."""
    cols = "\n".join(
        f"- {c['name']} ({c['type']})" + (f": {c['comment']}" if c.get("comment") else "") for c in schema["columns"]
    )
    rows = "\n".join(str(r) for r in schema.get("sample", []))
    return f"Table kpi_table, columns:\n{cols}" + (f"\nSample rows:\n{rows}" if rows else "")


def check_sql(sql: str) -> str | None:
    """Step 3a: None if `sql` is a single read-only SELECT, else the reason it is rejected."""
    try:
        statements = sqlglot.parse(sql, read="databricks")
    except sqlglot.errors.SqlglotError as e:
        return f"invalid SQL: {str(e).splitlines()[0]}"
    if len(statements) != 1 or not isinstance(statements[0], exp.Query):
        return "only a single SELECT query is allowed"
    if bad := statements[0].find(*_FORBIDDEN):
        return f"{type(bad).__name__.upper()} is not allowed, queries must be read-only"
    return None


def limit_rows(sql: str) -> str:
    """Step 3b: cap a checked query at MAX_ROWS rows; a smaller LIMIT written by the LLM is kept."""
    query = sqlglot.parse_one(sql, read="databricks")
    limit = query.args.get("limit")
    n = limit.expression if limit else None
    if not (isinstance(n, exp.Literal) and n.is_int and int(n.name) <= MAX_ROWS):
        query = query.limit(MAX_ROWS)
    return query.sql(dialect="databricks")


def make_query_kpi_table(llm, get_schema, run_sql):
    """Build the tool. llm: the agent's chat model; get_schema() -> schema dict; run_sql(sql) -> list of row dicts."""

    @tool
    def query_kpi_table(question: str) -> dict:
        """Answer a detail question about the KPI table (filtered rows, top-N, per-site or per-equipment
        breakdowns). Pass the question in plain words. Returns the SQL used and at most 200 rows."""
        description = describe_table(get_schema())  # 1. what the table looks like
        system, error = _SQL_PROMPT.format(description=description), None
        for _ in range(2):  # 2. LLM writes the SQL; one retry with the rejection reason
            human = question if not error else f"{question}\n\nYour previous query was rejected: {error}"
            sql = llm.invoke([SystemMessage(system), HumanMessage(human)]).content.strip().strip("`").removeprefix("sql").strip()
            if not (error := check_sql(sql)):  # 3. read-only guardrail, then the row cap
                sql = limit_rows(sql)
                return {"sql": sql, "rows": run_sql(sql)}  # 4. run (errors propagate, execute() reports them)
        return {"error": f"could not create a valid read-only query: {error}"}

    return query_kpi_table