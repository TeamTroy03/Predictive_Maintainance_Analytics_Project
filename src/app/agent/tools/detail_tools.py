"""KPI table detail tool for the Planner -> Judge -> Executor graph. `query_kpi_table` loops until a query is accepted:
an LLM writes SQL from the table descriptions, a parser guardrail accepts only a single read-only SELECT on the tables of
TABLES (the KPI table and its dimensions, so results can show names instead of keys) capped at 200 rows, Databricks runs
it, and any rejection or database error goes back to the LLM for a rewrite (at most
MAX_ATTEMPTS). There is no LLM code here: the caller passes in `write_sql`. The Databricks side (`get_schema`,
`run_detail_sql`) is at the bottom and only reads kpi_tools' `_table`. The two atomic tools of `make_kpi_tools` are the
same steps without the loop."""

import os
from decimal import Decimal
from functools import cache

import sqlglot
from langchain_core.tools import tool
from sqlglot import exp

from app.agent.tools.kpi_tools import _table

MAX_ROWS = 200
MAX_ATTEMPTS = 3
SAMPLE_ROWS = 3
TABLE = "kpi_table"  # the main table
# the only tables a query may read: name the LLM uses -> Gold table (run_detail_sql maps it to the real one)
TABLES = {TABLE: "kpi_equipment_month", "dim_equipment": "dim_equipment",
          "dim_equipment_type": "dim_equipment_type", "dim_site": "dim_site"}
RULES = (
    f"Rules: write one read-only SELECT using only these tables: {', '.join(TABLES)}. Join the dimensions to {TABLE} on "
    "their *_key columns and return readable names (equipment id, type and site names) instead of numeric keys. "
    "Dimension rows with key -1 are unknown members: exclude them. "
    f"If the user asks for a number of rows (top N, first N), end the query with LIMIT N, but never write a LIMIT above "
    f"{MAX_ROWS}; without a LIMIT, {MAX_ROWS} rows are returned."
)

# Anything that writes data or changes tables (DML/DDL/Command cover insert, update, delete, merge, create, drop, ...)
_FORBIDDEN = (exp.DML, exp.DDL, exp.Command, exp.Into)


def check_sql(sql: str) -> str | None:
    """None if `sql` is a single read-only SELECT on the tables of TABLES, else the reason it is rejected."""
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
    ctes = {c.alias for c in statements[0].find_all(exp.CTE)}  # a CTE name is not a real table
    if others := {exp.table_name(t) for t in statements[0].find_all(exp.Table)} - ctes - set(TABLES):
        return f"only the tables {', '.join(TABLES)} may be queried, not {', '.join(sorted(others))}"
    return None


def clean_sql(sql: str) -> str:
    """Drop the ```sql fences the LLM may add."""
    return sql.strip().strip("`").removeprefix("sql").strip()


def describe(schemas: dict) -> str:
    """get_schema() result as text for the LLM: the RULES, then per table one line per column "- name (type): comment"
    and the sample rows."""
    blocks = []
    for name, schema in schemas.items():
        cols = "\n".join(
            f"- {c['name']} ({c['type']})" + (f": {c['comment']}" if c.get("comment") else "") for c in schema["columns"]
        )
        rows = "\n".join(str(r) for r in schema.get("sample", []))  # sample rows are optional
        blocks.append(f"Table {name}:\n{cols}" + (f"\nSample rows:\n{rows}" if rows else ""))
    return RULES + "\n\n" + "\n\n".join(blocks)


def limit_rows(sql: str) -> str:
    """Cap a checked query at MAX_ROWS rows; a smaller LIMIT written by the LLM is kept."""
    query = sqlglot.parse_one(sql, read="databricks")
    limit = query.args.get("limit")
    n = limit.expression if limit else None  # the number in "LIMIT n"
    # keep only a plain integer <= MAX_ROWS; a missing, larger or non-literal limit is replaced
    if not (isinstance(n, exp.Literal) and n.is_int and int(n.name) <= MAX_ROWS):
        query = query.limit(MAX_ROWS)
    return query.sql(dialect="databricks")


def query_with_retries(question: str, get_schema, write_sql, run_sql, max_attempts: int = MAX_ATTEMPTS) -> dict:
    """Write, check and run SQL for `question` until Databricks accepts it, feeding each failure back to the writer.
    write_sql(question, schema_text, attempts) -> SQL text, where attempts is [{"sql", "error"}] of the failed tries;
    run_sql(sql) -> list of row dicts, raises on a database error."""
    schema, attempts = describe(get_schema()), []
    for n in range(1, max_attempts + 1):
        sql = clean_sql(write_sql(question, schema, attempts))
        if not (error := check_sql(sql)):
            sql = limit_rows(sql)
            try:
                rows = run_sql(sql)
                return {"sql": sql, "rows": rows, "row_count": len(rows), "attempts": n}
            except Exception as e:  # noqa: BLE001 - any database error (bad column, permissions...) goes back to the writer
                error = f"Databricks error: {str(e)[:300]}"
        else:
            error = f"Guardrail rejected query: {error}"
        attempts.append({"sql": sql, "error": error})
    return {"error": f"No valid query after {max_attempts} attempts.", "attempts": attempts}


def make_detail_tool(get_schema, write_sql, run_sql):
    """Build the `query_kpi_table` tool around query_with_retries (argument contracts there)."""

    @tool
    def query_kpi_table(question: str) -> dict:
        """Return up to 200 rows of the KPI table with equipment, type and site names (lists, breakdowns, rankings).
        Pass the question in plain words with months (YYYY-MM)."""
        return query_with_retries(question, get_schema, write_sql, run_sql)

    return query_kpi_table


def make_kpi_tools(get_schema):
    """Build [get_kpi_schema, validate_and_format_kpi_query], the atomic steps for an LLM that loops by itself.
    get_schema() -> {table name: {"columns": [{"name", "type", "comment"?}], "sample"?: [row dict]}}."""

    @tool
    def get_kpi_schema() -> str:
        """Return the columns (name, type, description) and sample rows of `kpi_table` and its dimension tables."""
        return describe(get_schema())

    @tool
    def validate_and_format_kpi_query(sql_query: str) -> dict:
        """Runs an AST safety guardrail to ensure only a single read-only SELECT
        statement is executed. Automatically enforces a limit of at most 200 rows.
        """
        cleaned_sql = clean_sql(sql_query)

        error = check_sql(cleaned_sql)
        if error:
            return {"error": f"Guardrail rejected query: {error}"}

        safe_sql = limit_rows(cleaned_sql)
        return {"valid": True, "sql": safe_sql}  # checked and limited SQL; nothing is run here

    return [get_kpi_schema, validate_and_format_kpi_query]


# ---------- Databricks ----------

def run_rows(sql: str) -> list[dict]:
    """Run one statement on Databricks and return all its rows as dicts (Decimal -> float, so numbers can be checked)."""
    # imported here so the module loads without the connector installed
    from databricks import sql as dbsql

    with dbsql.connect(
        server_hostname=os.environ["DATABRICKS_HOST"],
        http_path=os.environ["DATABRICKS_HTTP_PATH"],
        access_token=os.environ["DATABRICKS_TOKEN"],
    ) as conn, conn.cursor() as cursor:
        cursor.execute(sql)
        return [{k: float(v) if isinstance(v, Decimal) else v for k, v in row.asDict().items()}
                for row in cursor.fetchall()]


def run_detail_sql(sql: str) -> list[dict]:
    """Run SQL accepted by check_sql, with each name of TABLES replaced by its real Gold table. The token must be
    SELECT-only: the parser check is not the only protection."""
    tree = sqlglot.parse_one(sql, read="databricks")
    for t in tree.find_all(exp.Table):
        if t.name in TABLES and not t.db:  # CTE names and aliases are left alone
            real = sqlglot.to_table(_table(TABLES[t.name]))
            t.set("this", real.this)
            t.set("db", real.args["db"])
            t.set("catalog", real.args["catalog"])
    return run_rows(tree.sql(dialect="databricks"))


@cache  # ponytail: read once per process (one connection per query), restart to pick up new columns
def get_schema() -> dict:
    """Per table of TABLES: columns (name, type, comment) and SAMPLE_ROWS sample rows."""
    schemas = {}
    for name, gold in TABLES.items():
        table, columns = _table(gold), []
        for r in run_rows(f"DESCRIBE TABLE {table}"):
            if not r["col_name"] or r["col_name"].startswith("#"):  # DESCRIBE appends partition/metadata sections
                break
            columns.append({"name": r["col_name"], "type": r["data_type"], "comment": r["comment"]})
        schemas[name] = {"columns": columns, "sample": run_rows(f"SELECT * FROM {table} LIMIT {SAMPLE_ROWS}")}
    return schemas
