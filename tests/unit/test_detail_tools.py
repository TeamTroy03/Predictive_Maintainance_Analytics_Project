from types import SimpleNamespace

import pytest

from app.agent.tools import detail_tools as dt

SCHEMA = {
    "columns": [{"name": "site", "type": "string", "comment": "plant name"}, {"name": "mttr", "type": "double"}],
    "sample": [{"site": "A", "mttr": "2.5"}],
}


@pytest.mark.parametrize("sql", [
    "UPDATE kpi_table SET x = 1",
    "DROP TABLE kpi_table",
    "DELETE FROM kpi_table",
    "SELECT 1; DROP TABLE kpi_table",
    "INSERT INTO kpi_table VALUES (1)",
    "SELECT * INTO backup FROM kpi_table",
    "WITH d AS (DELETE FROM kpi_table RETURNING *) SELECT * FROM d",
    "SHOW TABLES",
])
def test_check_sql_rejects_writes(sql):
    assert dt.check_sql(sql)


def test_check_sql_accepts_select():
    assert dt.check_sql("SELECT site, AVG(mttr) FROM kpi_table GROUP BY site ORDER BY 2") is None


def test_limit_rows():
    assert dt.limit_rows("SELECT * FROM kpi_table").endswith("LIMIT 200")
    assert dt.limit_rows("SELECT * FROM kpi_table LIMIT 5").endswith("LIMIT 5")
    assert dt.limit_rows("SELECT * FROM kpi_table LIMIT 5000").endswith("LIMIT 200")


def test_describe_table():
    text = dt.describe_table(SCHEMA)
    assert "- site (string): plant name" in text and "- mttr (double)" in text and "'A'" in text


def test_query_kpi_table_end_to_end():
    replies = iter(["DROP TABLE kpi_table", "```sql\nSELECT site, mttr FROM kpi_table;\n```"])
    llm = SimpleNamespace(invoke=lambda messages: SimpleNamespace(content=next(replies)))
    ran = []
    tool = dt.make_query_kpi_table(llm, lambda: SCHEMA, lambda sql: ran.append(sql) or [{"site": "A"}])
    out = tool.invoke({"question": "mttr per site"})
    assert out == {"sql": "SELECT site, mttr FROM kpi_table LIMIT 200", "rows": [{"site": "A"}]}
    assert ran == [out["sql"]]  # the rejected DROP was never run


def test_query_kpi_table_gives_up_without_running():
    llm = SimpleNamespace(invoke=lambda messages: SimpleNamespace(content="DELETE FROM kpi_table"))
    tool = dt.make_query_kpi_table(llm, lambda: SCHEMA, lambda sql: pytest.fail("must not run"))
    assert "error" in tool.invoke({"question": "wipe it"})