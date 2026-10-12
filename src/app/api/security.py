"""Request authentication: session cookie (the website), API key (development) or Entra ID JWT (deployed design).

All behind one dependency, `require_auth`, so the routes never change. The checks themselves
are in `app.core.security`.
"""

import secrets

from fastapi import Depends, Security
from fastapi.security import APIKeyCookie, APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from app.api.errors import ApiError
from app.core.config import Settings, get_settings
from app.core.security import COOKIE_NAME, verify_jwt, verify_session_token

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
bearer = HTTPBearer(auto_error=False)
session_cookie = APIKeyCookie(name=COOKIE_NAME, auto_error=False)


def require_auth(
    api_key: str | None = Security(api_key_header),
    credentials: HTTPAuthorizationCredentials | None = Security(bearer),
    cookie: str | None = Security(session_cookie),
    settings: Settings = Depends(get_settings),
) -> str:
    """Returns who is calling ('user:<email>', 'jwt:<oid>' or 'api_key'); raises 401/403 otherwise."""
    if credentials and settings.jwt_enabled:
        return "jwt:" + str(verify_jwt(credentials.credentials, settings).get("oid", "user"))
    if cookie and settings.cookie_auth_enabled:
        return "user:" + verify_session_token(cookie, settings)
    valid_keys = settings.api_key_list()
    if api_key and any(secrets.compare_digest(api_key, k) for k in valid_keys):
        return "api_key"
    raise ApiError(401, "unauthorized", "Missing or invalid credentials")