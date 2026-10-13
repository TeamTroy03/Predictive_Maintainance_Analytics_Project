"""API contract: real HTTP requests to the app in-process (TestClient) with a fake Gold database that answers canned
template rows. Each test states what is sent and the status code and JSON shape that must come back; failures are
always {"error": {code, message, correlation_id, details}}.
"""

import re
from datetime import date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.api import agent as api_agent
from app.api import db as api_db
from app.api.db import GoldDB
from app.api.main import create_app
from app.api.routes import get_db
from app.core.config import Settings
from app.core.security import hash_password

KEY = {"X-API-Key": "test-key"}
EMAIL, PASSWORD = "max@example.com", "correct horse battery"
PASSWORD_HASH = hash_password(PASSWORD)  # scrypt is slow on purpose: hash once
TS = datetime(2026, 9, 21, 20, 4, 1)  # noqa: DTZ001 - naive on purpose, as the connector returns Gold's UTC
PLAN = "Approach: look up availability\n1. get_kpi(...)"

ASSET_KEYS = {"id", "name", "type", "line", "site", "status", "availability_pct", "manufacturer", "criticality",
              "installed", "active_alarms", "last_maintenance"}
ERROR_KEYS = {"code", "message", "correlation_id", "details"}

# ---------------------------------------------------------------- template rows
ASSET = {
    "equipment_id": "EQ-10002", "equipment_name": "Air Compressor AC-A0001", "manufacturer": "Nordwerk",
    "production_line": "L3", "criticality": "B", "install_date": date(2009, 9, 12), "site_name": "Rheinfeld Works",
    "equipment_type": "Air Compressor", "availability": Decimal("0.99931"), "alarms": 3,
    "asset_status": "operational", "last_maintenance": datetime(2026, 9, 1, 10, 0),  # noqa: DTZ001
}
KPI_VALUES = {"overdue_work_orders": 3, "unplanned_failures": 2, "work_orders": 40}  # every other KPI: 0.5
GROUP_VALUES = {"site_code": "PLT-B", "site_name": "Valmora", "equipment_type": "Pump", "equipment_id": "EQ-10002",
                "equipment_name": "Pump P-1"}


def kpi_rows(sql: str) -> list[dict]:
    """The columns run_kpis selects are the ' AS name' parts before FROM: answer exactly those."""
    names = re.findall(r" AS (\w+)", sql.split(" FROM ")[0])
    months = ["2026-01", "2026-02"] if "month" in names else [None]
    return [
        {n: TS if n in ("as_of_ts", "loaded_ts") else GROUP_VALUES.get(n, month if n == "month" else KPI_VALUES.get(n, 0.5))
         for n in names}
        for month in months
    ]


class FakeDB:
    """Stands in for GoldDB. Only EQ-10002 exists. An SQL it does not know fails the test."""

    def __init__(self):
        self.ok = True

    def table(self, name):
        return name

    def is_ready(self):
        return self.ok

    def query(self, sql, params=None):
        params = params or {}
        known = params.get("equipment_id") == "EQ-10002"
        if "asset_status AS status, COUNT(*)" in sql:
            return [{"status": "operational", "n": 5}, {"status": "critical", "n": 1}]
        if "COUNT(*) OVER () AS total" in sql:
            return [{**ASSET, "total": 1}]
        if "FROM assets WHERE equipment_id" in sql:
            return [ASSET] if known else []
        if "PARTITION BY sensor_id" in sql:
            return [{"sensor_id": "S1", "sensor_type": "vibration", "canonical_unit": "mm/s", "reading_value": 2.5,
                     "alarm_state": "normal"}]
        if "ORDER BY sensor_id, event_ts" in sql:
            return [{"sensor_id": "S1", "sensor_type": "vibration", "canonical_unit": "mm/s", "event_ts": TS,
                     "reading_value": 2.5, "is_alarm": False}]
        if sql.strip().startswith("SELECT equipment_key FROM"):
            return [{"equipment_key": 1}] if known else []
        if "is_cleared" in sql:
            return [{"event_ts": TS, "equipment_id": "EQ-10002", "sensor_id": "S1", "sensor_type": "vibration",
                     "reading_value": 9.1, "canonical_unit": "mm/s", "alarm_state": "alarm", "alarm_code": "VIB_HIGH",
                     "is_cleared": False}]
        if "ORDER BY event_ts DESC LIMIT 10" in sql:
            return [{"event_ts": TS, "sensor_type": "vibration", "reading_value": 9.1, "canonical_unit": "mm/s",
                     "alarm_state": "alarm", "alarm_code": "VIB_HIGH"}]
        if "is_open = TRUE" in sql:
            return [{"work_order_id": "WO-26001", "priority": "P1", "status": "Open", "due_ts": TS, "is_overdue": True}]
        if "wo_status" in sql:
            return [{"work_order_id": "WO-26001", "equipment_id": "EQ-10002", "priority": "P1", "wo_status": "overdue",
                     "maintenance_type": "Corrective", "failure_mode_code": "BRG", "failure_mode_desc": "Bearing wear",
                     "created_ts": TS, "due_ts": TS, "closed_ts": None, "downtime_hours": Decimal("3.5"),
                     "maintenance_cost_eur": Decimal("420.00")}]
        if "e.criticality, e.status" in sql:
            return [{"equipment_key": 1, "equipment_id": "EQ-10002", "equipment_name": "Air Compressor AC-A0001",
                     "criticality": "B", "status": "active", "site_code": "PLT-B", "site_name": "Rheinfeld Works",
                     "equipment_type": "Air Compressor"}] if known else []
        if "SELECT COUNT(*) AS n" in sql:
            return [{"n": 2}]
        if "ORDER BY w.priority" in sql:
            return [{"work_order_id": "WO-26001", "equipment_id": "EQ-10002", "site_code": "PLT-B", "priority": "P1",
                     "status": "Open", "failure_mode": "Bearing wear", "created_ts": TS, "due_ts": TS}]
        if "t.is_planned AS is_planned" in sql:
            return [{"type": "Corrective", "is_planned": False, "cost_eur": Decimal("420"), "work_orders": 4},
                    {"type": "Preventive", "is_planned": True, "cost_eur": Decimal("80"), "work_orders": 6}]
        if "MAX(k.as_of_ts) AS as_of_ts" in sql:
            return kpi_rows(sql)
        pytest.fail(f"FakeDB got an SQL it has no canned rows for: {sql[:200]}")


class FakeAgent:
    """Same functions as app.agent.agent, backed by a dict: no LLM, no Databricks."""

    def __init__(self):
        self.runs: dict[str, bool] = {}  # run_id -> waiting for approval

    def run_exists(self, run_id):
        return run_id in self.runs

    def pending_plan(self, run_id):
        return PLAN if self.runs.get(run_id) else None

    def start_run(self, question, run_id=None, conversation_id=None):
        from app.agent.schemas import AssistantAnswer
        if "weather" in question:  # judge rejects: nothing to approve
            self.runs[run_id] = False
            return run_id, None, {"answer": AssistantAnswer(
                answer="", evidence=[], filters={}, source_measure="",
                limitations=["Plan not approved (reject): out of scope."])}
        self.runs[run_id] = True
        return run_id, PLAN, {}

    def finish_run(self, run_id, approved):
        from app.agent.schemas import AssistantAnswer
        self.runs[run_id] = False
        if not approved:
            return {"answer": AssistantAnswer(answer="", evidence=[], filters={}, source_measure="",
                                              limitations=["The user declined the plan."])}
        return {
            "answer": AssistantAnswer(answer="Availability was 0.93.", evidence=["get_kpi: value=0.93"],
                                      filters={"start_month": "2026-04"}, source_measure="get_kpi", limitations=[]),
            "trace": [{"tool": "get_kpi", "args": {"kpi_name": "availability"}, "ok": True,
                       "result": {"value": 0.93, "unit": "ratio (0-1)", "row_count": 12}}],
        }


# ---------------------------------------------------------------- fixtures and helpers
def make_settings(**overrides) -> Settings:
    values = {"api_keys": SecretStr("test-key"), "jwt_secret": SecretStr("s" * 40),
              "auth_users": SecretStr(f"{EMAIL}={PASSWORD_HASH}")}
    return Settings(**{**values, **overrides})


@pytest.fixture
def db():
    return FakeDB()


@pytest.fixture
def client(db):
    app = create_app(make_settings())
    app.state.db = db  # the lifespan does not run without `with TestClient`
    app.dependency_overrides[get_db] = lambda: db
    # https base URL: the client only sends Secure cookies over https, like a browser
    return TestClient(app, base_url="https://testserver", raise_server_exceptions=False)


@pytest.fixture
def agent(monkeypatch):
    fake = FakeAgent()
    monkeypatch.setattr(api_agent, "_agent", lambda: fake)
    return fake


def assert_error(response, status, code=None):
    """The one error format: status, JSON {"error": {...}}, and the correlation ID in body and header."""
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == ERROR_KEYS
    assert body["error"]["correlation_id"] == response.headers["X-Correlation-ID"]
    assert "Traceback" not in response.text
    if code:
        assert body["error"]["code"] == code
    return body["error"]


def assert_keys(data, keys):
    assert set(data) == keys, f"{sorted(set(data) ^ keys)} differ"


# ---------------------------------------------------------------- 1-2 operations
def test_health_is_open_and_reports_the_environment(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "dev"}


def test_ready_is_200_when_gold_answers_and_503_when_not(client, db):
    ok = client.get("/ready")
    assert ok.status_code == 200 and ok.json() == {"status": "ready", "gold": True, "auth_configured": True}
    db.ok = False
    down = client.get("/ready")
    assert down.status_code == 503 and down.json() == {"status": "not_ready", "gold": False, "auth_configured": True}


# ---------------------------------------------------------------- 3-4 /kpis
def test_kpis_need_credentials(client):
    assert_error(client.get("/kpis"), 401, "unauthorized")
    assert_error(client.get("/kpis", headers={"X-API-Key": "wrong"}), 401, "unauthorized")


def test_kpis_list_the_12_governed_kpis(client):
    response = client.get("/kpis", headers=KEY)
    assert response.status_code == 200
    items = response.json()
    assert len(items) == 12 and all(set(i) == {"id", "unit", "description"} for i in items)
    assert {"availability", "mtbf_hours", "mttr_hours"} <= {i["id"] for i in items}


# ---------------------------------------------------------------- 5-6 login and session
def test_login_with_right_password_sets_the_cookie_and_returns_the_user(client):
    response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200
    assert_keys(response.json(), {"email", "name", "role"})
    assert "httponly" in response.headers["set-cookie"].lower()


def test_login_with_wrong_password_is_401_and_a_missing_field_is_422(client):
    wrong = client.post("/auth/login", json={"email": EMAIL, "password": "nope"})
    assert_error(wrong, 401, "unauthorized")
    assert "set-cookie" not in wrong.headers
    error = assert_error(client.post("/auth/login", json={"email": EMAIL}), 422, "validation_error")
    assert error["details"][0].keys() == {"field", "message"} and error["details"][0]["field"] == "password"


def test_me_needs_the_session_cookie(client):
    assert_error(client.get("/auth/me"), 401, "unauthorized")
    assert_error(client.get("/auth/me", headers=KEY), 401, "unauthorized")  # an API key is not a website user
    client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    me = client.get("/auth/me")
    assert me.status_code == 200 and me.json()["email"] == EMAIL


# ---------------------------------------------------------------- 7-8 /analytics/query
def test_analytics_query_returns_rows_for_the_requested_kpis_and_groups(client):
    body = {"kpis": ["availability", "mtbf_hours"], "group_by": ["site", "month"],
            "filters": {"month_from": "2025-01"}, "limit": 10}
    response = client.post("/analytics/query", json=body, headers=KEY)
    assert response.status_code == 200
    data = response.json()
    assert_keys(data, {"kpis", "rows", "row_count", "as_of_ts", "data_refreshed_ts", "source"})
    assert data["kpis"] == {"availability": "ratio", "mtbf_hours": "hours"}
    assert data["row_count"] == len(data["rows"]) == 2
    assert_keys(data["rows"][0], {"site_code", "site_name", "month", "availability", "mtbf_hours"})
    assert data["as_of_ts"].startswith("2026-09-21T20:04:01")


@pytest.mark.parametrize("body", [
    {"kpis": []},
    {"kpis": ["no_such_kpi"]},
    {"kpis": ["availability"], "limit": 5000},
    {"kpis": ["availability"], "sql": "DROP TABLE x"},
    {"kpis": ["availability"], "group_by": ["site", "month", "equipment"]},
    {"kpis": ["availability"], "filters": {"month_from": "2025-13"}},
    {"kpis": ["availability"], "filters": {"equipment_ids": ["x'; DROP TABLE y;--"]}},
], ids=["no-kpis", "unknown-kpi", "limit-too-big", "extra-field", "three-groups", "bad-month", "bad-equipment-id"])
def test_analytics_query_rejects_invalid_bodies_with_422(client, body):
    error = assert_error(client.post("/analytics/query", json=body, headers=KEY), 422, "validation_error")
    assert error["details"] and all(d.keys() == {"field", "message"} for d in error["details"])


# ---------------------------------------------------------------- 9-10 dashboard pages
def test_fleet_summary_shape(client):
    response = client.get("/fleet/summary", headers=KEY)
    assert response.status_code == 200
    data = response.json()
    assert_keys(data, {"as_of", "period_from", "period_to", "kpis", "status_counts", "availability_trend"})
    assert_keys(data["kpis"], {"availability_pct", "unplanned_downtime_h", "mtbf_h", "mttr_h", "repeat_failure_pct",
                               "overdue_work_orders", "maintenance_cost_eur", "alarm_rate_per_h"})
    assert data["status_counts"] == {"operational": 5, "warning": 0, "critical": 1, "offline": 0}
    assert (data["period_from"], data["period_to"]) == ("2026-01", "2026-02")
    assert_keys(data["availability_trend"][0], {"date", "availability_pct", "unplanned_downtime_h"})
    assert data["kpis"]["availability_pct"] == 50.0  # ratio 0.5 shown as percent


def test_maintenance_summary_shape(client):
    response = client.get("/maintenance/summary", headers=KEY)
    assert response.status_code == 200
    data = response.json()
    assert_keys(data, {"as_of", "period_from", "period_to", "kpis", "monthly", "cost_by_type"})
    assert_keys(data["kpis"], {"mtbf_h", "mttr_h", "overdue_work_orders", "maintenance_cost_eur",
                               "repeat_failure_pct", "planned_pct"})
    assert data["kpis"]["planned_pct"] == 60.0  # 6 of 10 work orders are planned
    assert_keys(data["monthly"][0], {"month", "mtbf_h", "mttr_h", "cost_eur"})
    assert data["cost_by_type"] == [{"type": "Corrective", "cost_eur": 420.0}, {"type": "Preventive", "cost_eur": 80.0}]


# ---------------------------------------------------------------- 11-14 equipment
def test_assets_page(client):
    response = client.get("/assets?status=critical&page=1&page_size=20", headers=KEY)
    assert response.status_code == 200
    data = response.json()
    assert_keys(data, {"items", "total", "page", "page_size"})
    assert (data["total"], data["page"], data["page_size"]) == (1, 1, 20)
    assert_keys(data["items"][0], ASSET_KEYS)
    assert data["items"][0]["availability_pct"] == 99.931


@pytest.mark.parametrize("query", ["page_size=500", "page_size=0", "page=0", "status=bogus", "q=" + "x" * 65])
def test_assets_reject_bad_query_parameters(client, query):
    assert_error(client.get(f"/assets?{query}", headers=KEY), 422, "validation_error")


def test_asset_detail_and_its_errors(client):
    ok = client.get("/assets/EQ-10002", headers=KEY)
    assert ok.status_code == 200
    data = ok.json()
    assert_keys(data, ASSET_KEYS | {"kpis", "latest_readings", "components"})
    assert_keys(data["kpis"], {"availability_pct", "mtbf_h", "mttr_h", "unplanned_downtime_h"})
    assert_keys(data["latest_readings"][0], {"sensor", "value", "unit", "status"})
    assert client.get("/assets/eq-10002", headers=KEY).json()["id"] == "EQ-10002"  # case does not matter
    assert_error(client.get("/assets/EQ-99999", headers=KEY), 404, "not_found")
    assert_error(client.get("/assets/abc", headers=KEY), 422, "validation_error")


def test_sensor_series_and_its_errors(client):
    response = client.get("/assets/EQ-10002/sensors?range=7d", headers=KEY)
    assert response.status_code == 200
    data = response.json()
    assert_keys(data, {"asset_id", "range", "sensors"})
    assert (data["asset_id"], data["range"]) == ("EQ-10002", "7d")
    assert_keys(data["sensors"][0], {"id", "name", "unit", "warn", "crit", "lower_is_bad", "points"})
    assert_keys(data["sensors"][0]["points"][0], {"t", "value", "anomaly"})
    assert_error(client.get("/assets/EQ-10002/sensors?range=2y", headers=KEY), 422, "validation_error")
    assert_error(client.get("/assets/EQ-99999/sensors", headers=KEY), 404, "not_found")


# ---------------------------------------------------------------- 15-16 alarms and work orders
def test_alarms_list(client):
    response = client.get("/alarms?asset_id=EQ-10002&limit=50", headers=KEY)
    assert response.status_code == 200
    alarm = response.json()[0]
    assert_keys(alarm, {"id", "asset_id", "severity", "sensor", "message", "value", "threshold", "unit", "time",
                        "acknowledged", "state"})
    assert (alarm["severity"], alarm["state"], alarm["message"]) == ("critical", "active", "Vibration high (alarm level)")


@pytest.mark.parametrize("query", ["limit=0", "limit=501", "asset_id=bogus"])
def test_alarms_reject_bad_query_parameters(client, query):
    assert_error(client.get(f"/alarms?{query}", headers=KEY), 422, "validation_error")


def test_work_orders_list(client):
    response = client.get("/maintenance/work-orders?status=overdue&limit=20", headers=KEY)
    assert response.status_code == 200
    item = response.json()[0]
    assert_keys(item, {"id", "asset_id", "type", "status", "priority", "title", "opened", "due", "completed",
                       "downtime_h", "cost"})
    assert (item["status"], item["title"], item["cost"]) == ("overdue", "Corrective – Bearing wear", 420.0)
    assert_error(client.get("/maintenance/work-orders?status=bogus", headers=KEY), 422, "validation_error")


# ---------------------------------------------------------------- 17-18 domain endpoints
def test_equipment_health_and_unknown_equipment(client):
    response = client.get("/equipment/EQ-10002/health", headers=KEY)
    assert response.status_code == 200
    data = response.json()
    assert_keys(data, {"equipment", "kpis", "recent_alarms", "open_work_orders", "as_of_ts"})
    assert data["equipment"]["equipment_id"] == "EQ-10002" and "equipment_key" not in data["equipment"]
    assert len(data["kpis"]) == 12 and data["recent_alarms"] and data["open_work_orders"]
    assert_error(client.get("/equipment/EQ-99999/health", headers=KEY), 404, "not_found")


def test_overdue_work_orders_page(client):
    response = client.get("/work-orders/overdue?priority=P1&site_code=PLT-B&page=1", headers=KEY)
    assert response.status_code == 200
    data = response.json()
    assert_keys(data, {"items", "page", "page_size", "total", "has_next"})
    assert (data["total"], data["page"], data["page_size"], data["has_next"]) == (2, 1, 20, False)
    assert_keys(data["items"][0], {"work_order_id", "equipment_id", "site_code", "priority", "status", "failure_mode",
                                   "created_ts", "due_ts"})
    assert client.get("/work-orders/overdue?page_size=1", headers=KEY).json()["has_next"] is True
    assert_error(client.get("/work-orders/overdue?priority=P9", headers=KEY), 422, "validation_error")


# ---------------------------------------------------------------- 19-20 assistant
def ask(client, question="What was the fleet availability?", **headers):
    return client.post("/assistant/query", json={"question": question}, headers={**KEY, **headers})


def test_assistant_query_returns_a_plan_waiting_for_approval(client, agent):
    response = ask(client)
    assert response.status_code == 200
    data = response.json()
    assert_keys(data, {"status", "run_id", "plan", "result"})
    assert (data["status"], data["plan"], data["result"]) == ("awaiting_approval", PLAN, None)
    assert data["run_id"] == response.headers["X-Correlation-ID"]  # the run ID is the request's correlation ID


def test_assistant_out_of_scope_question_is_answered_without_a_plan(client, agent):
    data = ask(client, "What is the weather?").json()
    assert data["status"] == "answered" and data["plan"] is None
    assert data["result"]["grounded"] is False and "out of scope" in data["result"]["limitations"][0]


@pytest.mark.parametrize("body", [{}, {"question": ""}, {"question": "x" * 1001}, {"question": "ok", "extra": 1},
                                  {"question": "ok", "conversation_id": "bad id!"}])
def test_assistant_query_rejects_invalid_bodies(client, agent, body):
    assert_error(client.post("/assistant/query", json=body, headers=KEY), 422, "validation_error")


def test_assistant_approve_runs_the_plan_and_returns_the_answer(client, agent):
    run_id = ask(client).json()["run_id"]
    response = client.post("/assistant/approve", json={"run_id": run_id, "approved": True}, headers=KEY)
    assert response.status_code == 200
    data = response.json()
    assert_keys(data, {"status", "run_id", "plan", "result"})
    assert (data["status"], data["run_id"], data["plan"]) == ("answered", run_id, None)
    result = data["result"]
    assert_keys(result, {"answer", "evidence", "grounded", "limitations", "filters", "source_measure"})
    assert result["grounded"] is True and result["source_measure"] == "get_kpi"
    assert_keys(result["evidence"][0], {"tool", "arguments", "status", "result"})


def test_assistant_decline_answers_with_a_limitation_and_no_evidence(client, agent):
    run_id = ask(client).json()["run_id"]
    result = client.post("/assistant/approve", json={"run_id": run_id, "approved": False}, headers=KEY).json()["result"]
    assert result["evidence"] == [] and result["grounded"] is False and "declined" in result["limitations"][0]


def test_assistant_approve_errors(client, agent):
    assert_error(client.post("/assistant/approve", json={"run_id": "nope", "approved": True}, headers=KEY), 404,
                 "unknown_run")
    run_id = ask(client).json()["run_id"]
    client.post("/assistant/approve", json={"run_id": run_id, "approved": True}, headers=KEY)
    assert_error(client.post("/assistant/approve", json={"run_id": run_id, "approved": True}, headers=KEY), 409,
                 "run_not_pending")
    for body in ({"run_id": run_id}, {"approved": True}, {"run_id": "bad id!", "approved": True},
                 {"run_id": run_id, "approved": True, "extra": 1}):
        assert_error(client.post("/assistant/approve", json=body, headers=KEY), 422, "validation_error")


def test_assistant_needs_credentials(client, agent):
    assert_error(client.post("/assistant/query", json={"question": "hi"}), 401, "unauthorized")
    assert_error(client.post("/assistant/approve", json={"run_id": "x", "approved": True}), 401, "unauthorized")


# ---------------------------------------------------------------- 21-22 error format and failures
def test_a_client_correlation_id_is_echoed_and_an_unsafe_one_is_replaced(client):
    mine = client.get("/assets/EQ-99999", headers={**KEY, "X-Correlation-ID": "trace-abc.1"})
    assert mine.headers["X-Correlation-ID"] == "trace-abc.1"
    assert assert_error(mine, 404)["correlation_id"] == "trace-abc.1"
    unsafe = client.get("/health", headers={"X-Correlation-ID": "bad id with spaces"})
    assert re.fullmatch(r"[0-9a-f-]{36}", unsafe.headers["X-Correlation-ID"])  # a fresh UUID


def test_unknown_path_and_wrong_method_use_the_error_format(client):
    assert_error(client.get("/nope", headers=KEY), 404, "not_found")
    assert_error(client.delete("/kpis", headers=KEY), 405, "method_not_allowed")


def test_a_gold_connection_error_is_503_without_details(client, monkeypatch):
    def boom(**_):
        raise ConnectionError("secret-host.azuredatabricks.net refused")

    monkeypatch.setattr(api_db.databricks_sql, "connect", boom)
    client.app.dependency_overrides[get_db] = lambda: GoldDB(make_settings(query_retries=0))
    response = client.get("/fleet/summary", headers=KEY)
    assert_error(response, 503, "data_unavailable")
    assert "secret-host" not in response.text and "ConnectionError" not in response.text


def test_an_unexpected_exception_is_a_clean_500(client, db):
    def explode(*_, **__):
        raise RuntimeError("internal detail that must not leak")

    db.query = explode
    response = client.get("/fleet/summary", headers=KEY)
    assert_error(response, 500, "internal_error")
    assert "internal detail" not in response.text
