"""Typed tools returning row-level detail (equipment, work orders, sensor readings) from Gold.

Runs LLM-written SQL over the KPI tables kpi_tools has loaded into TABLES; never calls Databricks.
"""

import logging
import sqlite3
import time

from langchain_core.tools import tool

log = logging.getLogger(__name__)

MAX_ROWS = 200
TIMEOUT_S = 5
_ALLOWED = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION}


# Hand-off point: kpi_tools puts the KPI tables it fetched here as {name: [row dicts]}.
# ponytail: plain dict, no TTL/eviction; kpi_tools owns freshness (cache) later.
TABLES: dict[str, list[dict]] = {}


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _connect(tables: dict[str, list[dict]]) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    for name, rows in tables.items():
        if not rows:
            continue
        cols = list(rows[0])
        conn.execute(f"CREATE TABLE {_q(name)} ({', '.join(map(_q, cols))})")
        conn.executemany(
            f"INSERT INTO {_q(name)} VALUES ({', '.join('?' * len(cols))})",
            [[v if v is None or isinstance(v, (int, float, str)) else str(v) for v in (r.get(c) for c in cols)] for r in rows],
        )
    # Lock down only after loading: read-only SELECT, no ATTACH/PRAGMA/DDL/writes.
    conn.set_authorizer(lambda action, *_: sqlite3.SQLITE_OK if action in _ALLOWED else sqlite3.SQLITE_DENY)
    deadline = time.monotonic() + TIMEOUT_S
    conn.set_progress_handler(lambda: time.monotonic() > deadline, 10_000)
    return conn


def describe_kpi_tables() -> str:
    """Schema text (`table(col, col, ...)`) of the loaded KPI tables, for the planner prompt."""
    return "\n".join(f"{t}({', '.join(rows[0])})" for t, rows in TABLES.items() if rows)


@tool
def query_kpi_tables(sql: str) -> dict:
    """Drill into the KPI tables already fetched by get_kpi (call get_kpi first).

    Pass ONE read-only SQLite SELECT over those tables, e.g. group/sort/filter for row-level detail.
    Returns columns, rows (max 200), row_count, truncated and the executed sql, or an error.
    """
    start = time.monotonic()
    if not any(TABLES.values()):
        return {"error": "no KPI data loaded; call get_kpi first"}
    conn = _connect(TABLES)
    try:
        cur = conn.execute(sql)
        rows = cur.fetchmany(MAX_ROWS + 1)
        result = {
            "columns": [d[0] for d in cur.description or []],
            "rows": [list(r) for r in rows[:MAX_ROWS]],
            "row_count": min(len(rows), MAX_ROWS),
            "truncated": len(rows) > MAX_ROWS,
            "sql": sql,
        }
        log.info("query_kpi_tables ok rows=%d ms=%d sql=%.200s", result["row_count"], (time.monotonic() - start) * 1000, sql)
        return result
    except (sqlite3.Error, sqlite3.Warning) as e:  # bad SQL, denied statement, timeout, multi-statement
        log.warning("query_kpi_tables rejected (%s) sql=%.200s", type(e).__name__, sql)
        return {"error": "query rejected: only a single valid read-only SELECT over the KPI tables is allowed"}
    finally:
        conn.close()