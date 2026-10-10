"""Authentication: API key (development) or Entra ID JWT (deployed design).

Both behind one dependency, `require_auth`, so the routes never change.
"""

import secrets

import jwt
from fastapi import Depends, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.errors import ApiError

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer = HTTPBearer(auto_error=False)
_jwks_clients: dict[str, jwt.PyJWKClient] = {}


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


def require_auth(
    api_key: str | None = Security(api_key_header),
    credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    settings: Settings = Depends(get_settings),
) -> str:
    """Returns who is calling; raises 401/403 otherwise."""
    if credentials and settings.jwt_enabled:
        return "jwt:" + str(verify_jwt(credentials.credentials, settings).get("oid", "user"))
    valid_keys = settings.api_key_list()
    if api_key and any(secrets.compare_digest(api_key, k) for k in valid_keys):
        return "api_key"
    raise ApiError(401, "unauthorized", "Missing or invalid credentials")
