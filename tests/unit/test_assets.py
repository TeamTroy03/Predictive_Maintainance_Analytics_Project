"""Equipment, sensor, alarm and work-order endpoints. No Databricks: the DB answers canned rows by SQL marker."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import assets, shaping
from app.core.config import Settings
from app.api.errors import ApiError
from app.api.main import create_app
from app.api.routes import get_db
from app.api.schemas import AnalyticsResult

HEADERS = {"X-API-Key": "test-key"}
TS = datetime(2026, 9, 21, 20, 4, 1)  # noqa: DTZ001 - naive on purpose, as the connector returns Gold's UTC

ASSET = {
    "equipment_id": "EQ-10002", "equipment_name": "Air Compressor AC-A0001", "manufacturer": "Nordwerk",
    "production_line": "L3", "criticality": "B", "install_date": date(2009, 9, 12), "site_name": "Rheinfeld Works",
    "equipment_type": "Air Compressor", "availability": Decimal("0.99931"), "alarms": 3,
    "asset_status": "operational", "last_maintenance": datetime(2026, 9, 1, 10, 0),  # noqa: DTZ001
}
KPI_ROW = {"availability": 0.99931, "mtbf_hours": 1200.5, "mttr_hours": 4.25, "unplanned_downtime_hours": 8.5}


class FakeDB:
    """rules: (marker in the SQL, rows). First match wins, so list the specific markers first."""

    def __init__(self, rules):
        self.rules, self.calls = rules, []

    def table(self, name):
        return name

    def query(self, sql, params=None):
        self.calls.append((sql, params or {}))
        return next((rows for marker, rows in self.rules if marker in sql), [])


@pytest.fixture(autouse=True)
def fake_kpis(monkeypatch):
    def run(_db, q, digits=4):
        rows = [KPI_ROW] if q.filters.equipment_ids else []
        return AnalyticsResult(kpis={}, rows=rows, row_count=len(rows), as_of_ts=TS, data_refreshed_ts=TS)

    monkeypatch.setattr(shaping, "run_kpis", run)


# ---------------------------------------------------------------- assets
def test_assets_are_mapped_and_paged():
    db = FakeDB([("asset_status", [{**ASSET, "total": 9489}])])
    page = assets.list_assets(db, page=3, page_size=50)
    row = page.items[0]
    assert (row.id, row.name, row.type, row.line, row.site) == (
        "EQ-10002", "Air Compressor AC-A0001", "Air Compressor", "L3", "Rheinfeld Works")
    assert (row.status, row.availability_pct, row.active_alarms) == ("operational", 99.931, 3)
    assert row.installed == date(2009, 9, 12) and row.last_maintenance.tzinfo is UTC
    assert (page.total, page.page, page.page_size) == (9489, 3, 50)
    assert "OFFSET 100" in db.calls[0][0] and "LIMIT 50" in db.calls[0][0]


def test_asset_filters_are_bound_parameters():
    db = FakeDB([("asset_status", [])])
    assets.list_assets(db, status="critical", q="Pump%", has_sensors=True)
    sql, params = db.calls[0]
    assert params["status"] == "critical" and params["q"] == "%pump%%"  # lower-cased, never in the SQL text
    assert "pump" not in sql.lower() and "fact_sensor_reading" in sql
    assert params["operational"] == assets.OPERATIONAL_FROM and params["warning"] == assets.WARNING_FROM


def test_asset_detail_has_converted_kpis_and_latest_readings():
    db = FakeDB([
        ("ROW_NUMBER", [{"sensor_id": "EQ-10002-VIB01", "sensor_type": "vibration", "canonical_unit": "mm/s",
                         "reading_value": 7.5, "alarm_state": "alarm"}]),
        ("asset_status", [ASSET]),
    ])
    detail = assets.asset_detail(db, "EQ-10002")
    assert detail.kpis.availability_pct == 99.931 and detail.kpis.mtbf_h == 1200.5
    reading = detail.latest_readings[0]
    assert (reading.sensor, reading.value, reading.unit, reading.status) == ("Vibration", 7.5, "mm/s", "critical")
    assert detail.components == []


def test_unknown_asset_is_404():
    with pytest.raises(ApiError) as e:
        assets.asset_detail(FakeDB([]), "EQ-99999")
    assert e.value.status == 404


# ---------------------------------------------------------------- sensors
SERIES = [
    {"sensor_id": "EQ-1-TMP01", "sensor_type": "temperature", "canonical_unit": "degC", "event_ts": TS, "reading_value": 61.5, "is_alarm": False},
    {"sensor_id": "EQ-1-VIB01", "sensor_type": "vibration", "canonical_unit": "mm/s", "event_ts": TS, "reading_value": 9.1, "is_alarm": True},
    {"sensor_id": "EQ-1-VIB01", "sensor_type": "vibration", "canonical_unit": "mm/s", "event_ts": TS, "reading_value": 2.0, "is_alarm": False},
]


def test_sensor_series_groups_by_sensor_and_keeps_gold_flags_only():
    db = FakeDB([("ORDER BY sensor_id", SERIES), ("SELECT equipment_key", [{"equipment_key": 42}])])
    result = assets.sensor_series(db, "EQ-10002", "7d")
    assert [(s.id, s.name, s.unit) for s in result.sensors] == [
        ("EQ-1-TMP01", "Temperature", "degC"), ("EQ-1-VIB01", "Vibration", "mm/s")]
    vibration = result.sensors[1]
    assert [p.anomaly for p in vibration.points] == [True, False]
    assert vibration.points[0].t.tzinfo is UTC
    assert vibration.warn is None and vibration.crit is None  # Gold has no limits: none invented


def test_sensor_window_ends_at_the_last_reading_not_at_now():
    db = FakeDB([("ORDER BY sensor_id", []), ("SELECT equipment_key", [{"equipment_key": 42}])])
    assets.sensor_series(db, "EQ-10002", "24h")
    assert "TIMESTAMPADD(HOUR, -24, (SELECT MAX(event_ts)" in db.calls[-1][0]
    assets.sensor_series(db, "EQ-10002", "all")
    assert "TIMESTAMPADD" not in db.calls[-1][0]


def test_sensor_series_of_unknown_asset_is_404():
    with pytest.raises(ApiError):
        assets.sensor_series(FakeDB([]), "EQ-99999", "24h")


# ---------------------------------------------------------------- alarms
def test_alarms_get_severity_message_and_no_invented_fields():
    row = {"event_ts": TS, "equipment_id": "EQ-18463", "sensor_id": "EQ-18463-VIB01", "sensor_type": "vibration",
           "reading_value": 9.1, "canonical_unit": "mm/s", "alarm_state": "alarm", "alarm_code": "VIB_HIGH",
           "is_cleared": False}
    db = FakeDB([("is_alarm = TRUE", [row, {**row, "alarm_state": "warning", "alarm_code": None, "sensor_type": "load",
                                            "is_cleared": True}])])
    critical, warning = assets.list_alarms(db, "EQ-18463", 50)
    assert (critical.severity, warning.severity) == ("critical", "warning")
    assert (critical.state, warning.state) == ("active", "cleared")  # cleared = a later normal reading exists
    assert critical.message == "Vibration high (alarm level)" and warning.message == "Load warning"
    assert critical.id.startswith("EQ-18463-VIB01|")  # sensor + reading time: Gold has no alarm id
    assert critical.threshold is None and critical.acknowledged is False and critical.time.tzinfo is UTC
    assert db.calls[0][1] == {"equipment_id": "EQ-18463"} and "LIMIT 50" in db.calls[0][0]


# ---------------------------------------------------------------- work orders
def wo(**over):
    return {"work_order_id": "WO-1", "equipment_id": "EQ-10002", "priority": "P2", "wo_status": "overdue",
            "maintenance_type": "Corrective", "failure_mode_code": "BRG-WEAR", "failure_mode_desc": "Bearing wear",
            "created_ts": TS, "due_ts": TS, "closed_ts": None, "downtime_hours": Decimal("5.25"),
            "maintenance_cost_eur": Decimal("1200.50"), **over}


def test_work_orders_get_a_built_title_and_numbers():
    db = FakeDB([("fact_maintenance_work_order", [wo(), wo(work_order_id="WO-2", failure_mode_code="NONE",
                 failure_mode_desc="No failure", maintenance_type="Preventive", downtime_hours=None, wo_status="completed")])])
    corrective, preventive = assets.list_work_orders(db, status="overdue", equipment_id="EQ-10002", limit=20)
    assert corrective.title == "Corrective – Bearing wear" and corrective.status == "overdue"
    assert (corrective.downtime_h, corrective.cost) == (5.25, 1200.5)
    assert preventive.title == "Preventive maintenance" and preventive.downtime_h is None
    params = db.calls[0][1]
    assert params == {"status": "overdue", "equipment_id": "EQ-10002"} and "LIMIT 20" in db.calls[0][0]


# ---------------------------------------------------------------- HTTP
@pytest.fixture
def client():
    db = FakeDB([("ROW_NUMBER", []), ("asset_status", []), ("SELECT equipment_key", [])])
    app = create_app(Settings(api_keys=SecretStr("test-key")))
    app.dependency_overrides[get_db] = lambda: db
    client = TestClient(app)
    client.db = db
    return client


@pytest.mark.parametrize(
    "path",
    ["/assets", "/assets/EQ-10002", "/assets/EQ-10002/sensors", "/alarms", "/maintenance/work-orders"],
)
def test_endpoints_need_auth(client, path):
    assert client.get(path).status_code == 401


def test_unknown_asset_is_a_404_with_the_standard_error_shape(client):
    response = client.get("/assets/EQ-10002", headers=HEADERS)
    assert response.status_code == 404 and response.json()["error"]["code"] == "not_found"


@pytest.mark.parametrize(
    "path",
    ["/assets/not-an-id", "/assets/EQ-10002/sensors?range=1y", "/assets?status=broken", "/assets?page_size=1000",
     "/alarms?asset_id=x", "/maintenance/work-orders?status=weird"],
)
def test_bad_input_is_a_422(client, path):
    assert client.get(path, headers=HEADERS).status_code == 422


def test_list_is_cached_but_free_text_search_is_not(client):
    client.get("/assets", headers=HEADERS)
    calls = len(client.db.calls)
    client.get("/assets", headers=HEADERS)
    assert len(client.db.calls) == calls  # same page again: from cache
    client.get("/assets?q=pump", headers=HEADERS)
    client.get("/assets?q=pump", headers=HEADERS)
    assert len(client.db.calls) == calls + 2  # every distinct search would otherwise grow the cache