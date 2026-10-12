"""Cookie login: JWT in an HttpOnly + Secure cookie, users from AUTH_USERS. No Databricks."""

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.core.config import Settings
from app.api.main import create_app
from app.core.security import ISSUER, hash_password, verify_password

EMAIL = "max@example.com"
PASSWORD = "correct horse battery"
SECRET = "s" * 40
PROTECTED = "/kpis"  # needs auth, no database


def settings(**overrides) -> Settings:
    values = {
        "api_keys": SecretStr("test-key"),
        "jwt_secret": SecretStr(SECRET),
        "auth_users": SecretStr(f"{EMAIL}={hash_password(PASSWORD)}"),
    }
    return Settings(**{**values, **overrides})


@pytest.fixture
def client():
    # https base URL: the client only sends Secure cookies over https, like a browser
    return TestClient(create_app(settings()), base_url="https://testserver")


def login(client, password=PASSWORD, email=EMAIL):
    return client.post("/auth/login", json={"email": email, "password": password})


def token(secret=SECRET, email=EMAIL, issuer=ISSUER, expires=timedelta(minutes=5), algorithm="HS256"):
    now = datetime.now(UTC)
    claims = {"sub": email, "iss": issuer, "iat": now, "exp": now + expires}
    return jwt.encode(claims, None if algorithm == "none" else secret, algorithm=algorithm)


# ---------------------------------------------------------------- passwords
def test_password_hash_roundtrip_and_unique_salt():
    first, second = hash_password(PASSWORD), hash_password(PASSWORD)
    assert first != second and PASSWORD not in first
    assert verify_password(PASSWORD, first) and not verify_password("wrong", first)
    assert not verify_password(PASSWORD, "garbage")


# ---------------------------------------------------------------- login
def test_login_sets_httponly_secure_strict_cookie(client):
    response = login(client)
    assert response.status_code == 200 and response.json()["email"] == EMAIL
    cookie = response.headers["set-cookie"].lower()
    assert cookie.startswith("access_token=")
    for flag in ("httponly", "secure", "samesite=strict", "path=/", "max-age=3600"):
        assert flag in cookie
    assert PASSWORD not in response.text


def test_cookie_is_not_secure_only_when_configured_for_local_http():
    local = TestClient(create_app(settings(cookie_secure=False)))
    assert "secure" not in login(local).headers["set-cookie"].lower().split("; ")


def test_wrong_password_and_unknown_user_look_identical(client):
    wrong = login(client, password="nope")
    unknown = login(client, email="nobody@example.com")
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json()["error"]["code"] == unknown.json()["error"]["code"]
    assert wrong.json()["error"]["message"] == unknown.json()["error"]["message"]
    assert "set-cookie" not in wrong.headers


def test_email_is_case_insensitive(client):
    assert login(client, email="MAX@Example.com").status_code == 200


def test_login_rejects_unknown_fields_without_echoing_the_password(client):
    response = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD, "admin": True})
    assert response.status_code == 422 and PASSWORD not in response.text


def test_login_unavailable_when_not_configured():
    bare = TestClient(create_app(Settings(api_keys=SecretStr("test-key"))), base_url="https://testserver")
    assert login(bare).status_code == 503


def test_login_is_throttled_even_for_the_right_password(client):
    assert [login(client, password="nope").status_code for _ in range(5)] == [401] * 5
    blocked = login(client)
    assert blocked.status_code == 429 and "retry-after" in blocked.headers


# ---------------------------------------------------------------- the cookie as credential
def test_cookie_grants_access_and_no_cookie_does_not(client):
    assert client.get(PROTECTED).status_code == 401
    assert client.get("/auth/me").status_code == 401
    login(client)
    assert client.get(PROTECTED).status_code == 200
    assert client.get("/auth/me").json()["email"] == EMAIL


def test_logout_clears_the_cookie(client):
    login(client)
    response = client.post("/auth/logout")
    assert response.status_code == 200
    assert "max-age=0" in response.headers["set-cookie"].lower()
    assert client.get("/auth/me").status_code == 401


@pytest.mark.parametrize(
    "bad",
    [
        token(expires=timedelta(minutes=-1)),  # expired
        token(secret="x" * 40),  # signed with another key
        token(algorithm="none"),  # unsigned
        token(issuer="someone-else"),  # wrong issuer
        token(email="removed@example.com"),  # valid token of a user no longer in AUTH_USERS
        "not-a-jwt",
    ],
    ids=["expired", "other-key", "alg-none", "issuer", "removed-user", "garbage"],
)
def test_invalid_cookie_is_rejected(client, bad):
    client.cookies.set("access_token", bad)
    assert client.get(PROTECTED).status_code == 401


def test_good_token_is_accepted(client):
    client.cookies.set("access_token", token())
    assert client.get(PROTECTED).status_code == 200


def forged_with_empty_key() -> str:
    """HS256 token signed with an empty key, built by hand (PyJWT refuses to sign with one)."""
    now = int(datetime.now(UTC).timestamp())

    def b64(raw: bytes) -> bytes:
        return base64.urlsafe_b64encode(raw).rstrip(b"=")

    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps({"sub": EMAIL, "iss": ISSUER, "iat": now, "exp": now + 300}).encode())
    signature = hmac.new(b"", head + b"." + body, hashlib.sha256).digest()
    return b".".join([head, body, b64(signature)]).decode()


def test_server_without_a_secret_never_accepts_a_forged_cookie():
    unconfigured = TestClient(create_app(settings(jwt_secret=SecretStr(""))), base_url="https://testserver")
    unconfigured.cookies.set("access_token", forged_with_empty_key())
    assert unconfigured.get(PROTECTED).status_code == 401


def test_api_key_still_works(client):
    assert client.get(PROTECTED, headers={"X-API-Key": "test-key"}).status_code == 200
    assert client.get(PROTECTED, headers={"X-API-Key": "wrong"}).status_code == 401


# ---------------------------------------------------------------- production guard
@pytest.mark.parametrize(
    "overrides",
    [{"cookie_secure": False}, {"jwt_secret": SecretStr("short")}, {"auth_users": SecretStr("")}],
    ids=["insecure-cookie", "weak-secret", "no-users"],
)
def test_prod_refuses_to_start_with_unsafe_auth(overrides):
    with pytest.raises(RuntimeError):
        create_app(settings(environment="prod", **overrides))


def test_prod_starts_with_safe_auth():
    create_app(settings(environment="prod"))
