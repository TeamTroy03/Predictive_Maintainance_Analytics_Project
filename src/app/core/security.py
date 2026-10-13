"""Security logic without FastAPI: passwords, login throttle, session token, Entra ID JWT.

The request-facing side (headers, cookie, `require_auth`) is in `app.api.security`. The HS256 session
JWT is issued by POST /auth/login for users listed by hand in AUTH_USERS.
"""

import hashlib
import secrets
import time
from datetime import UTC, datetime, timedelta

import jwt

from app.api.errors import ApiError
from app.core.config import Settings

COOKIE_NAME = "access_token"
ISSUER = "assets-api"

_jwks_clients: dict[str, jwt.PyJWKClient] = {}


# ---------------------------------------------------------------- passwords (stdlib scrypt)
def _scrypt(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)


def hash_password(password: str) -> str:
    """'scrypt$<salt hex>$<hash hex>', the value of an AUTH_USERS entry."""
    salt = secrets.token_bytes(16)
    return f"scrypt${salt.hex()}${_scrypt(password, salt).hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt, digest = stored.split("$")
        return scheme == "scrypt" and secrets.compare_digest(_scrypt(password, bytes.fromhex(salt)).hex(), digest)
    except ValueError:
        return False


_UNKNOWN_USER_HASH = hash_password(secrets.token_hex(8))


def authenticate(email: str, password: str, settings: Settings) -> bool:
    """Always runs one scrypt, so the response time does not reveal which emails exist."""
    stored = settings.users().get(email.strip().lower())
    return verify_password(password, stored or _UNKNOWN_USER_HASH) and stored is not None


# ---------------------------------------------------------------- login throttle
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 15 * 60


def _recent(failures: dict[str, list[float]], key: str) -> list[float]:
    cutoff = time.monotonic() - FAILURE_WINDOW_SECONDS
    recent = [t for t in failures.get(key, []) if t > cutoff]
    if recent:
        failures[key] = recent
    else:
        failures.pop(key, None)  # expired entries disappear, so the dict stays small
    return recent


# ponytail: in-process counters, fine for the single worker the in-memory agent already requires;
# a shared store (Redis) if the API is ever scaled out.
def is_throttled(failures: dict[str, list[float]], key: str) -> bool:
    return len(_recent(failures, key)) >= MAX_FAILURES


def record_failure(failures: dict[str, list[float]], key: str) -> None:
    failures[key] = [*_recent(failures, key), time.monotonic()]


# ---------------------------------------------------------------- session token (our own JWT)
def issue_token(email: str, settings: Settings) -> str:
    now = datetime.now(UTC)
    claims = {"sub": email, "iss": ISSUER, "iat": now, "exp": now + timedelta(minutes=settings.session_minutes)}
    return jwt.encode(claims, settings.jwt_secret.get_secret_value(), algorithm="HS256")


def verify_session_token(token: str, settings: Settings) -> str:
    """The signed-in email. The algorithm is pinned, so 'none' or RS256 tokens never validate."""
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=["HS256"],
            issuer=ISSUER,
            options={"require": ["exp", "iat", "sub", "iss"]},
        )
    except jwt.PyJWTError as exc:
        raise ApiError(401, "unauthorized", "Invalid or expired session") from exc
    if claims["sub"] not in settings.users():  # deleting a user from AUTH_USERS ends their session
        raise ApiError(401, "unauthorized", "Invalid or expired session")
    return claims["sub"]


# ---------------------------------------------------------------- Entra ID JWT (deployed design)
def signing_key(token: str, tenant_id: str):  # noqa: ANN201 - replaced in tests
    """Public key of Entra ID that signed the token (keys are cached)."""
    url = f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"
    client = _jwks_clients.setdefault(url, jwt.PyJWKClient(url, cache_keys=True, timeout=5))
    return client.get_signing_key_from_jwt(token).key


def verify_jwt(token: str, settings: Settings) -> dict:
    try:
        claims = jwt.decode(
            token,
            signing_key(token, settings.jwt_tenant_id),
            algorithms=["RS256"],
            audience=settings.jwt_audience,
            issuer=f"https://login.microsoftonline.com/{settings.jwt_tenant_id}/v2.0",
        )
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        raise ApiError(401, "unauthorized", "Invalid or expired token") from exc
    granted = set(claims.get("roles", [])) | set(str(claims.get("scp", "")).split())
    if settings.jwt_required_role not in granted:
        raise ApiError(403, "forbidden", f"Token lacks role '{settings.jwt_required_role}'")
    return claims