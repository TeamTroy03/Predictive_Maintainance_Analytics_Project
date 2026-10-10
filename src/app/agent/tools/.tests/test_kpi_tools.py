"""Live tests for the get_kpi tool: every query runs against the real Gold tables in Databricks.
Needs DATABRICKS_HOST, DATABRICKS_HTTP_PATH, DATABRICKS_TOKEN, GOLD_CATALOG, GOLD_SCHEMA (and GOLD_TABLE_PREFIX if it is
not "test_") in the environment or in the project's .env. Without them every test is skipped.
Run: pytest src/app/agent/tools/.tests -v

No expected numbers are hard-coded (Gold is rebuilt on every pipeline run). Instead the tests check what must always
hold: all 12 KPIs run, values are in their valid range, KPIs are consistent with each other, additive KPIs add up over
months and filters only narrow the result."""

import os
from itertools import pairwise

import pytest
from dotenv import load_dotenv

from app.agent.tools.kpi_tools import KPIS, _table, get_kpi, run_query

load_dotenv()

REQUIRED = ("DATABRICKS_HOST", "DATABRICKS_HTTP_PATH", "DATABRICKS_TOKEN", "GOLD_CATALOG", "GOLD_SCHEMA")
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not all(os.getenv(k) for k in REQUIRED), reason=f"needs {', '.join(REQUIRED)}"),
]


def month_text(date_key: int) -> str:
    return f"{str(date_key)[:4]}-{str(date_key)[4:6]}"


def months_between(first: str, last: str) -> list[str]:
    year, month = int(first[:4]), int(first[5:])
    out = []
    while f"{year:04d}-{month:02d}" <= last:
        out.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def kpi(name: str, start: str, end: str, **filters) -> dict:
    """Call the real tool (agent path: tool -> compute_kpi -> Databricks)."""
    result = get_kpi.invoke({"kpi_name": name, "start_month": start, "end_month": end, **filters})
    assert "error" not in result, result
    return result


@pytest.fixture(scope="session")
def gold():
    """Real values from Gold to build requests with: the month range with data and one equipment with its type."""
    span = run_query(
        f"SELECT MIN(month_start_date_key) AS first_key, MAX(month_start_date_key) AS last_key "
        f"FROM {_table('kpi_equipment_month')}", {})
    if span["first_key"] is None:
        pytest.skip("the Gold KPI table is empty")
    equipment = run_query(
        f"SELECT e.equipment_id, t.equipment_type FROM {_table('dim_equipment')} e "
        f"JOIN {_table('dim_equipment_type')} t ON t.equipment_type_key = e.equipment_type_key "
        f"WHERE e.equipment_key <> -1 AND e.site_key <> -1 AND e.equipment_type_key <> -1 "
        f"AND e.equipment_key IN (SELECT equipment_key FROM {_table('kpi_equipment_month')}) "
        f"ORDER BY e.equipment_id LIMIT 1", {})
    start, end = month_text(span["first_key"]), month_text(span["last_key"])
    return {"start": start, "end": end, "months": months_between(start, end), **equipment}


@pytest.fixture(scope="session")
def quiet_equipment_id():
    """An equipment with no unplanned failure at all (the zero-denominator case), skips if there is none."""
    row = run_query(
        f"SELECT e.equipment_id FROM {_table('dim_equipment')} e JOIN "
        f"(SELECT equipment_key FROM {_table('kpi_equipment_month')} GROUP BY equipment_key "
        f"HAVING SUM(unplanned_failure_count) = 0) k ON k.equipment_key = e.equipment_key "
        f"ORDER BY e.equipment_id LIMIT 1", {})
    if row is None:
        pytest.skip("every equipment has at least one unplanned failure")
    return row["equipment_id"]


# ---------- every KPI runs against Gold ----------

@pytest.mark.parametrize("name", list(KPIS))
def test_every_kpi_runs_on_the_whole_fleet(name, gold):
    result = kpi(name, gold["start"], gold["end"])
    assert result["kpi_name"] == name
    assert result["row_count"] > 0
    assert result["value"] is None or isinstance(result["value"], float)
    assert result["unit"] == KPIS[name]["unit"]
    assert KPIS[name]["formula"] in result["source_measure"]
    assert result["as_of_ts"] and result["gold_loaded_ts"]
    assert result["filters"] == {"start_month": gold["start"], "end_month": gold["end"]}
    assert result["limitations"]


@pytest.mark.parametrize("name", list(KPIS))
def test_every_kpi_runs_with_all_filters(name, gold):
    result = kpi(name, gold["start"], gold["end"],
                 equipment_type=gold["equipment_type"], equipment_id=gold["equipment_id"])
    assert result["row_count"] > 0  # the equipment was picked from the KPI table, so it has rows
    assert result["value"] is None or isinstance(result["value"], float)


# ---------- values must be plausible ----------

@pytest.mark.parametrize("name", ["availability", "repeat_failure_rate"])
def test_ratios_are_between_0_and_1(name, gold):
    value = kpi(name, gold["start"], gold["end"])["value"]
    assert value is not None and 0 <= value <= 1


@pytest.mark.parametrize("name", [n for n in KPIS if n != "availability"])
def test_other_kpis_are_not_negative(name, gold):
    value = kpi(name, gold["start"], gold["end"])["value"]
    assert value is None or value >= 0


def test_warning_and_critical_alarm_rates_add_up_to_alarm_rate(gold):
    total, warning, critical = (kpi(n, gold["start"], gold["end"])["value"]
                                for n in ("alarm_rate", "alarm_warning_rate", "alarm_critical_rate"))
    assert warning + critical == pytest.approx(total, rel=1e-9)


def test_mttr_equals_unplanned_hours_per_failure(gold):
    hours, failures, mttr = (kpi(n, gold["start"], gold["end"])["value"]
                             for n in ("unplanned_downtime_hours", "unplanned_failures", "mttr_hours"))
    if failures:
        assert mttr == pytest.approx(hours / failures, rel=1e-9)
    else:
        assert mttr is None  # zero failures: undefined, not 0


@pytest.mark.parametrize("name", ["mtbf_hours", "mttr_hours", "repeat_failure_rate"])
def test_zero_failures_return_none_with_a_limitation(name, gold, quiet_equipment_id):
    """Divide by zero must give None (never 0 or infinity) plus a limitation."""
    assert kpi("unplanned_failures", gold["start"], gold["end"], equipment_id=quiet_equipment_id)["value"] == 0
    result = kpi(name, gold["start"], gold["end"], equipment_id=quiet_equipment_id)
    assert result["row_count"] > 0 and result["value"] is None
    assert any("denominator is zero" in x for x in result["limitations"])


# ---------- rollups and filters ----------

@pytest.mark.parametrize("name", ["unplanned_failures", "work_orders", "maintenance_cost_eur", "unplanned_downtime_hours"])
def test_additive_kpis_over_a_range_equal_the_sum_of_the_months(name, gold):
    months = gold["months"][:12]
    whole = kpi(name, months[0], months[-1])["value"]
    per_month = sum(kpi(name, m, m)["value"] or 0 for m in months)
    assert whole == pytest.approx(per_month, rel=1e-6)


@pytest.mark.parametrize("name", ["work_orders", "unplanned_failures", "alarm_rate"])
def test_filters_only_narrow_the_result(name, gold):
    fleet = kpi(name, gold["start"], gold["end"])["row_count"]
    by_type = kpi(name, gold["start"], gold["end"], equipment_type=gold["equipment_type"])["row_count"]
    one = kpi(name, gold["start"], gold["end"], equipment_type=gold["equipment_type"],
              equipment_id=gold["equipment_id"])["row_count"]
    assert 0 < one <= by_type <= fleet


def test_type_and_equipment_filters_ignore_case(gold):
    exact = kpi("work_orders", gold["start"], gold["end"], equipment_id=gold["equipment_id"])["value"]
    lower = kpi("work_orders", gold["start"], gold["end"], equipment_id=gold["equipment_id"].lower())["value"]
    assert lower == exact
    by_type = kpi("work_orders", gold["start"], gold["end"], equipment_type=gold["equipment_type"])["value"]
    assert kpi("work_orders", gold["start"], gold["end"], equipment_type=gold["equipment_type"].upper())["value"] == by_type


def test_single_month_request(gold):
    result = kpi("availability", gold["end"], gold["end"])
    assert result["row_count"] > 0


def test_each_month_of_the_range_can_be_queried(gold):
    for first, second in pairwise(gold["months"][:6]):
        assert kpi("alarm_rate", first, second)["row_count"] > 0


# ---------- no data / bad input ----------

def test_unknown_equipment_returns_none_and_a_limitation(gold):
    result = kpi("availability", gold["start"], gold["end"], equipment_id="NO-SUCH-EQUIPMENT")
    assert result["row_count"] == 0 and result["value"] is None
    assert any("No Gold rows" in x for x in result["limitations"])


def test_months_without_data_return_none(gold):
    result = kpi("work_orders", "1990-01", "1990-02")
    assert result["row_count"] == 0 and result["value"] is None


@pytest.mark.parametrize("args", [
    {"kpi_name": "availability", "start_month": "2026-03", "end_month": "2026-01"},
    {"kpi_name": "availability", "start_month": "2026-13", "end_month": "2026-14"},
    {"kpi_name": "availability", "start_month": "2026-01", "end_month": "2026-02", "equipment_id": "EQ1'; DROP TABLE x--"},
])
def test_bad_requests_return_an_error_without_a_query(args):
    assert "error" in get_kpi.invoke(args)
