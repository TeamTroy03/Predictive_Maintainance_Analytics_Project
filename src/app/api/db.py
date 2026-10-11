"""Access to the Gold tables in Databricks (the only module that talks to the data).

Uses a Databricks SQL warehouse through databricks-sql-connector:
* every query has a statement timeout,
* temporary connection problems are retried a bounded number of times with backoff,
* user values are ALWAYS bound parameters (:name); table names come from a fixed list.
"""

import logging
import time
from typing import Any

from databricks import sql as databricks_sql

from app.api.config import Settings
from app.api.errors import ApiError

logger = logging.getLogger("api.db")

GOLD_TABLES = (
    "dim_date",
    "dim_site",
    "dim_equipment_type",
    "dim_equipment",
    "dim_failure_mode",
    "dim_maintenance_type",
    "fact_sensor_reading",
    "fact_maintenance_work_order",
    "fact_downtime",
    "kpi_equipment_month",
)
_TEMPORARY_ERRORS = ("RequestError", "OperationalError", "TimeoutError", "ConnectionError")


class GoldDB:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def table(self, name: str) -> str:
        """Full name of a Gold table, e.g. data2026acc123456.team3.test_gold_dim_site."""
        if name not in GOLD_TABLES:
            raise ValueError(f"{name} is not a Gold table")
        return f"{self.settings.gold_schema}.{self.settings.gold_table_prefix}{name}"

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Run a SELECT and return the rows as dictionaries."""
        attempts = 1 + self.settings.query_retries
        for attempt in range(1, attempts + 1):
            try:
                return self._run(sql, params or {})
            except Exception as exc:
                temporary = type(exc).__name__ in _TEMPORARY_ERRORS or isinstance(exc, OSError)
                logger.warning("gold_query_failed", extra={"error": type(exc).__name__, "attempt": attempt})
                if not temporary or attempt == attempts:
                    raise ApiError(503, "data_unavailable", "Gold data is unavailable") from exc
                time.sleep(0.5 * 2 ** (attempt - 1))  # backoff: 0.5s, 1s, ...
        return []

    def _run(self, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        s = self.settings
        with (
            databricks_sql.connect(
                server_hostname=s.databricks_host,
                http_path=s.databricks_http_path,
                access_token=s.databricks_token.get_secret_value(),
                session_configuration={"STATEMENT_TIMEOUT": str(s.query_timeout_seconds)},
            ) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(sql, params)
            columns = [c[0] for c in cursor.description]
            return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]

    def is_ready(self) -> bool:
        """Readiness: the Gold KPI table answers a trivial query."""
        try:
            self.query(f"SELECT 1 AS ok FROM {self.table('kpi_equipment_month')} LIMIT 1")
            return True
        except Exception:  # noqa: BLE001
            return False
