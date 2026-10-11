"""Dashboard endpoints: display conversions of the governed KPIs. No Databricks: run_kpis and the DB are fakes."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import dashboard, services, shaping
from app.api.config import Settings
from app.api.main import create_app
from app.api.routes import get_db
from app.api.schemas import AnalyticsQuery, AnalyticsResult

HEADERS = {"X-API-Key": "test-key"}
AS_OF = datetime(2026, 9, 21, 22, 55, tzinfo=UTC)

# run_kpis rows by group_by, as GoldDB returns them (ratios 0-1, alarm rate per asset-day)
FLEET_ROW = {
    "availability": 0.964,
    "unplanned_downtime_hours": 38.25,
    "mtbf_hours": 742.0,
    "mttr_hours": 2.4,
    "repeat_failure_rate": 0.048,
    "overdue_work_orders": 6,
    "maintenance_cost_eur": 25700.0,
    "alarm_rate": 24.0,
}
MONTHS = [
    {"month": "2026-03", "availability": 0.95, "unplanned_downtime_hours": 10.0,
     "mtbf_hours": 600.0, "mttr_hours": 3.0, "maintenance_cost_eur": 100.0},
    {"month": "2026-04", "availability": 0.97, "unplanned_downtime_hours": 5.0,
     "mtbf_hours": None, "mttr_hours": None, "maintenance_cost_eur": 50.0},
]
TYPES = [
    {"type": "Preventive", "is_planned": True, "cost_eur": 60.0, "work_orders": 60},
    {"type": "Predictive", "is_planned": True, "cost_eur": 25.0, "work_orders": 25},
    {"type": "Corrective", "is_planned": False, "cost_eur": 80.0, "work_orders": 15},
]


class FakeDB:
    def __init__(self, *, rows=None, statuses=None, types=None):
        self.rows = {(): [FLEET_ROW], ("month",): MONTHS} if rows is None else rows
        self.statuses = [{"status": "operational", "n": 7}, {"status": "critical", "n": 1}] if statuses is None else statuses
        self.types = TYPES if types is None else types
        self.queries = 0

    def table(self, name):
        return name

    def query(self, sql, params=None):
        self.queries += 1
        return self.statuses if "AS status" in sql else self.types


@pytest.fixture
def db(monkeypatch):
    fake = FakeDB()

    def fake_run_kpis(_db, q, digits=4):
        fake.queries += 1
        fake.digits = digits
        rows = fake.rows.get(tuple(g.value for g in q.group_by), [])
        return AnalyticsResult(kpis={}, rows=rows, row_count=len(rows),
                               as_of_ts=AS_OF if rows else None, data_refreshed_ts=AS_OF if rows else None)

    monkeypatch.setattr(shaping, "run_kpis", fake_run_kpis)
    return fake


# ---------------------------------------------------------------- precision
def test_run_kpis_rounds_to_4_digits_unless_asked_for_more():
    class RawDB(FakeDB):
        def query(self, sql, params=None):  # the warehouse answer: a ratio with many digits
            return [{"repeat_failure_rate": 0.10802259, "as_of_ts": AS_OF, "loaded_ts": AS_OF}]

    q = AnalyticsQuery(kpis=["repeat_failure_rate"])
    assert services.run_kpis(RawDB(), q).rows[0]["repeat_failure_rate"] == 0.108
    assert services.run_kpis(RawDB(), q, digits=8).rows[0]["repeat_failure_rate"] == 0.10802259


def test_dashboard_converts_before_rounding(db):
    # 0.0001202595 per asset-day is ~5e-6 per hour: rounding to 4 digits first (or after) would show 0
    db.rows[()] = [{**FLEET_ROW, "alarm_rate": 0.0001202595, "repeat_failure_rate": 0.108023}]
    s = dashboard.fleet_summary(db)
    assert s.kpis.alarm_rate_per_h == pytest.approx(5.011e-6, rel=1e-3)
    assert s.kpis.repeat_failure_pct == 10.8023
    assert db.digits == 8


# ---------------------------------------------------------------- fleet
def test_fleet_converts_units(db):
    s = dashboard.fleet_summary(db)
    assert s.kpis.availability_pct == 96.4  # ratio -> percent
    assert s.kpis.repeat_failure_pct == 4.8
    assert s.kpis.alarm_rate_per_h == 1.0  # 24 per asset-day -> 1 per hour
    assert (s.kpis.mtbf_h, s.kpis.mttr_h, s.kpis.overdue_work_orders) == (742.0, 2.4, 6)
    assert s.as_of == AS_OF


def test_fleet_trend_is_monthly_with_period(db):
    s = dashboard.fleet_summary(db)
    assert [p.date for p in s.availability_trend] == ["2026-03-01", "2026-04-01"]
    assert [p.availability_pct for p in s.availability_trend] == [95.0, 97.0]
    assert (s.period_from, s.period_to) == ("2026-03", "2026-04")


def test_fleet_status_counts_fill_missing_with_zero(db):
    c = dashboard.fleet_summary(db).status_counts
    assert (c.operational, c.warning, c.critical, c.offline) == (7, 0, 1, 0)


def test_fleet_without_data_is_empty_not_zero(db):
    db.rows, db.statuses = {}, []
    s = dashboard.fleet_summary(db)
    assert s.kpis.availability_pct is None and s.as_of is None
    assert s.availability_trend == [] and s.period_from is None


# ---------------------------------------------------------------- maintenance
def test_maintenance_monthly_keeps_null_when_no_failures(db):
    m = dashboard.maintenance_summary(db)
    assert m.monthly[0].model_dump() == {"month": "2026-03", "mtbf_h": 600.0, "mttr_h": 3.0, "cost_eur": 100.0}
    assert m.monthly[1].mtbf_h is None  # no failures: MTBF undefined, not 0


def test_maintenance_planned_share_and_cost_by_type(db):
    m = dashboard.maintenance_summary(db)
    assert m.kpis.planned_pct == 85.0  # (60 + 25) of 100 work orders
    assert [(t.type, t.cost_eur) for t in m.cost_by_type] == [
        ("Preventive", 60.0), ("Predictive", 25.0), ("Corrective", 80.0)]


# ---------------------------------------------------------------- HTTP
@pytest.fixture
def client(db):
    app = create_app(Settings(api_keys=SecretStr("test-key")))
    app.dependency_overrides[get_db] = lambda: db
    return TestClient(app)


def test_endpoints_need_auth(client):
    assert client.get("/fleet/summary").status_code == 401
    assert client.get("/maintenance/summary").status_code == 401


def test_endpoints_return_contract_shape(client):
    fleet = client.get("/fleet/summary", headers=HEADERS).json()
    assert {"as_of", "kpis", "status_counts", "availability_trend"} <= fleet.keys()
    maintenance = client.get("/maintenance/summary", headers=HEADERS).json()
    assert {"kpis", "monthly", "cost_by_type"} <= maintenance.keys()


def test_second_call_is_served_from_cache(client, db):
    client.get("/fleet/summary", headers=HEADERS)
    queries = db.queries
    client.get("/fleet/summary", headers=HEADERS)
    assert db.queries == queries
