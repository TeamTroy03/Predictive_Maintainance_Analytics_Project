"""Helpers shared by the endpoints that shape Gold data for the frontend pages."""

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.api.db import GoldDB
from app.api.kpis import Kpi
from app.api.schemas import AnalyticsQuery, AnalyticsResult, GroupBy
from app.api.services import run_kpis


def kpi_rows(
    db: GoldDB, kpis: Sequence[Kpi], group_by: Sequence[GroupBy] = (), filters: dict[str, Any] | None = None
) -> AnalyticsResult:
    # 8 digits: values are converted afterwards (x100, /24), so they are rounded once, after the conversion
    query = AnalyticsQuery(kpis=list(kpis), group_by=list(group_by), filters=filters or {}, limit=1000)
    return run_kpis(db, query, digits=8)


def scale(value: float | None, factor: float = 1.0, digits: int = 4) -> float | None:
    """None stays None: an undefined KPI (zero denominator) must not turn into a 0."""
    return None if value is None else round(float(value) * factor, digits)


def utc(value: datetime | None) -> datetime | None:
    """Gold timestamps are UTC but come back naive; marked as UTC they serialize with a Z, so browsers read them right."""
    return None if value is None else value.replace(tzinfo=UTC)