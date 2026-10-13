"""Security checks that need no cloud: the assistant's SQL guardrail, route protection, auth edge cases, log redaction
and SQL injection. No Databricks, no LLM."""

import json
import logging
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.agent.tools import detail_tools as dt
from app.api import assets, services
from app.api.kpis import Kpi
from app.api.main import create_app
from app.api.schemas import AnalyticsFilters, AnalyticsQuery
from app.core import security
from app.core.config import Settings
from app.core.logging import JsonFormatter
from app.core.security import hash_password

HOSTILE = "x'; DROP TABLE t;--"
KEY = {"X-API-Key": "test-key"}


class FakeDB:
    """Records every query; answers no rows (only the COUNT query of a page gets its one row)."""

    def __init__(self):
        self.calls = []

    def table(self, name):
        return name

    def query(self, sql, params=None):
        self.calls.append((sql, params or {}))
        return [{"n": 0}] if "COUNT(*) AS n" in sql else []


# ---------------------------------------------------------------- 1. SQL guardrail of the assistant
@pytest.mark.parametrize("sql", [
    "WITH secrets AS (SELECT * FROM secrets) SELECT * FROM secrets",  # CTE named like the real table it reads
    "WITH k AS (SELECT * FROM kpi_table) SELECT * FROM k, secrets",
    "SELECT * FROM kpi_table UNION SELECT * FROM secrets",
    "SELECT * FROM kpi_table k JOIN other_schema.secrets s ON k.a = s.a",
    "SELECT * FROM system.information_schema.tables",
    "SELECT * FROM read_files('abfss://x/y')",
    "SELECT * FROM range(1000000000)",
    "SELECT ai_query('model', 'prompt') FROM kpi_table",
    "SELECT reflect('java.lang.System', 'getenv') FROM kpi_table",
    "EXPLAIN SELECT * FROM kpi_table",
    "COPY INTO t FROM 'abfss://x/y'",
    "SET spark.sql.shuffle.partitions = 1",
    "USE CATALOG other",
    "SELECT * FROM kpi_table; SELECT * FROM secrets",
    "-- comment\nDROP TABLE kpi_table",
    "",
])
def test_check_sql_rejects_hostile_queries(sql):
    assert dt.check_sql(sql)


@pytest.mark.parametrize("sql", [
    "SELECT site, AVG(mttr) FROM kpi_table GROUP BY site ORDER BY 2",
    "WITH a AS (SELECT * FROM kpi_table), b AS (SELECT * FROM a) SELECT * FROM b",
    "SELECT k.site, s.site_name FROM kpi_table k JOIN dim_site s ON k.site_key = s.site_key WHERE s.site_key <> -1",
    "SELECT ROUND(SUM(a) / NULLIF(SUM(b), 0), 2), DATE_TRUNC('month', CURRENT_DATE()) FROM kpi_table",
])
def test_check_sql_still_accepts_legitimate_queries(sql):
    assert dt.check_sql(sql) is None


# ---------------------------------------------------------------- 2. every protected route needs credentials
PUBLIC = {"/health", "/ready", "/auth/login", "/auth/logout", "/docs", "/redoc", "/openapi.json",
          "/docs/oauth2-redirect"}


def protected_routes(app):
    """(method, path) of every operation in the OpenAPI schema that is not public; path params filled in."""
    return [
        (method.upper(), path.replace("{equipment_id}", "EQ-10002"))
        for path, ops in app.openapi()["paths"].items() if path not in PUBLIC
        for method in ops
    ]


def test_every_protected_route_returns_401_without_credentials():
    app = create_app(Settings(api_keys=SecretStr("test-key")))
    client = TestClient(app)
    routes = protected_routes(app)
    assert len(routes) >= 10  # the schema was really read
    for method, path in routes:
        response = client.request(method, path, json={} if method == "POST" else None)
        assert response.status_code == 401, f"{method} {path} -> {response.status_code}"
        assert response.json()["error"]["code"] == "unauthorized"


# ---------------------------------------------------------------- 3. auth edge cases
def test_non_ascii_api_key_is_a_401_not_a_500():
    client = TestClient(create_app(Settings(api_keys=SecretStr("test-key"))), raise_server_exceptions=False)
    assert client.get("/kpis", headers={"X-API-Key": "café".encode()}).status_code == 401


PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
TENANT, AUDIENCE = "tenant-1", "api://pm"


@pytest.fixture
def entra(monkeypatch):
    """Client with Entra bearer auth on; the signing key is our local public key instead of Microsoft's JWKS."""
    monkeypatch.setattr(security, "signing_key", lambda token, tenant: PRIVATE_KEY.public_key())
    settings = Settings(api_keys=SecretStr("test-key"), jwt_tenant_id=TENANT, jwt_audience=AUDIENCE,
                        jwt_secret=SecretStr("s" * 40), auth_users=SecretStr(f"max@example.com={hash_password('pw')}"))
    return TestClient(create_app(settings), base_url="https://testserver")


def entra_token(roles=("PM.Reader",), audience=AUDIENCE, tenant=TENANT, expires=timedelta(minutes=5), key=PRIVATE_KEY):
    now = datetime.now(UTC)
    claims = {"aud": audience, "iss": f"https://login.microsoftonline.com/{tenant}/v2.0", "oid": "abc",
              "roles": list(roles), "iat": now, "exp": now + expires}
    return jwt.encode(claims, key, algorithm="RS256")


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_entra_token_with_the_required_role_is_accepted(entra):
    assert entra.get("/kpis", headers=bearer(entra_token())).status_code == 200


def test_entra_token_without_the_role_is_forbidden(entra):
    response = entra.get("/kpis", headers=bearer(entra_token(roles=("Other",))))
    assert response.status_code == 403 and response.json()["error"]["code"] == "forbidden"


@pytest.mark.parametrize("bad", [
    entra_token(audience="api://other"),
    entra_token(tenant="tenant-2"),
    entra_token(expires=timedelta(minutes=-1)),
    entra_token(key=rsa.generate_private_key(public_exponent=65537, key_size=2048)),  # signed by someone else
    "not-a-jwt",
], ids=["audience", "issuer", "expired", "other-key", "garbage"])
def test_invalid_entra_token_is_rejected(entra, bad):
    response = entra.get("/kpis", headers=bearer(bad))
    assert response.status_code == 401 and str(bad) not in response.text


def test_bearer_wins_over_cookie_and_a_bad_credential_does_not_fall_through(entra):
    entra.cookies.set("access_token", "garbage")
    # a good bearer is enough, even next to a garbage cookie and a wrong key
    assert entra.get("/kpis", headers={**bearer(entra_token()), "X-API-Key": "wrong"}).status_code == 200
    # a bad bearer fails closed: a valid API key next to it does not rescue the request
    assert entra.get("/kpis", headers={**bearer("garbage"), **KEY}).status_code == 401
    # a bad cookie fails closed too
    assert entra.get("/kpis", headers=KEY).status_code == 401


# ---------------------------------------------------------------- 4. production guard from the environment
def test_prod_environment_without_auth_refuses_to_start(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "prod")
    with pytest.raises(RuntimeError):
        create_app(Settings())


# ---------------------------------------------------------------- 5. secrets in logs, error bodies, CORS
def test_log_fields_with_secret_names_are_redacted():
    record = logging.makeLogRecord({"msg": "login", "api_key": "k-123", "db_token": "t-456",
                                    "Authorization": "Bearer abc", "password": "pw", "user": "max"})
    line = json.loads(JsonFormatter().format(record))
    assert line["api_key"] == line["db_token"] == line["Authorization"] == line["password"] == "***"
    assert line["user"] == "max" and "k-123" not in json.dumps(line)


def test_error_bodies_never_echo_the_credential_sent():
    client = TestClient(create_app(Settings(api_keys=SecretStr("test-key"))))
    for headers in ({"X-API-Key": "my-wrong-key"}, bearer("my.wrong.token")):
        response = client.get("/kpis", headers=headers)
        assert response.status_code == 401
        assert "my-wrong-key" not in response.text and "my.wrong.token" not in response.text
        assert "test-key" not in response.text


def test_no_cors_headers_even_for_a_foreign_origin():
    client = TestClient(create_app(Settings(api_keys=SecretStr("test-key"))))
    origin = {"Origin": "https://evil.example"}
    assert "access-control-allow-origin" not in client.get("/health", headers=origin).headers
    preflight = client.options("/kpis", headers={**origin, "Access-Control-Request-Method": "GET"})
    assert "access-control-allow-origin" not in preflight.headers


# ---------------------------------------------------------------- 6. SQL injection: user values are only ever parameters
def test_run_kpis_binds_filters_even_when_validation_is_bypassed():
    filters = AnalyticsFilters.model_construct(site_codes=[HOSTILE], equipment_ids=[HOSTILE],
                                               month_from=HOSTILE, month_to=HOSTILE)
    query = AnalyticsQuery.model_construct(kpis=[Kpi.availability], group_by=[], filters=filters, limit=10)
    db = FakeDB()
    services.run_kpis(db, query)
    sql, params = db.calls[0]
    assert "DROP" not in sql and HOSTILE not in sql
    assert list(params.values()).count(HOSTILE) == 4


def test_overdue_work_orders_binds_site_code():
    db = FakeDB()
    services.overdue_work_orders(db, HOSTILE, "P1", 1, 20)
    assert all("DROP" not in sql and HOSTILE.upper() not in sql for sql, _ in db.calls)
    assert all(params["site_code"] == HOSTILE.upper() for _, params in db.calls)


def test_list_assets_binds_the_search_text():
    db = FakeDB()
    assets.list_assets(db, q=HOSTILE)
    sql, params = db.calls[0]
    assert "drop" not in sql.lower() and HOSTILE.lower() not in sql.lower()
    assert params["q"] == f"%{HOSTILE.lower()}%"


@pytest.mark.parametrize("method, path, body", [
    ("POST", "/analytics/query", {"kpis": ["availability"], "filters": {"site_codes": [HOSTILE]}}),
    ("POST", "/analytics/query", {"kpis": ["availability; DROP TABLE t"]}),
    ("GET", "/work-orders/overdue?site_code=" + HOSTILE, None),
    ("GET", "/alarms?asset_id=" + HOSTILE, None),
    ("GET", "/assets/" + HOSTILE, None),
])
def test_hostile_values_are_rejected_at_the_api_edge(method, path, body):
    db = FakeDB()
    app = create_app(Settings(api_keys=SecretStr("test-key")))
    from app.api.routes import get_db
    app.dependency_overrides[get_db] = lambda: db
    response = TestClient(app).request(method, path, json=body, headers=KEY)
    assert response.status_code == 422 and not db.calls
