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


def test_get_kpi_schema():
    get_schema_tool, _ = dt.make_kpi_tools(lambda: SCHEMA)
    text = get_schema_tool.invoke({})
    assert "- site (string): plant name" in text and "- mttr (double)" in text and "'A'" in text


def test_validate_kpi_query_strips_fences_checks_and_limits():
    _, validate = dt.make_kpi_tools(lambda: SCHEMA)
    out = validate.invoke({"sql_query": "```sql\nSELECT site, mttr FROM kpi_table;\n```"})
    assert out == {"valid": True, "sql": "SELECT site, mttr FROM kpi_table LIMIT 200"}


def test_validate_kpi_query_rejects_writes():
    _, validate = dt.make_kpi_tools(lambda: SCHEMA)
    out = validate.invoke({"sql_query": "DELETE FROM kpi_table"})
    assert "error" in out and "valid" not in out
