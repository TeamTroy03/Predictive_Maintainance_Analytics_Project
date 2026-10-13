"""Unit tests never read the developer's .env: a real JWT_SECRET or AUTH_USERS in it would change their outcome.
(Integration tests are outside this folder and keep using the real .env.)"""

import pytest

from app.core.config import Settings

Settings.model_config["env_file"] = None

# agent.py calls load_dotenv(), which would leak the developer's .env into os.environ for every later test
_AUTH_ENV = ("AUTH_USERS", "JWT_SECRET", "COOKIE_SECURE", "API_KEYS", "JWT_TENANT_ID", "JWT_AUDIENCE", "ENVIRONMENT")


@pytest.fixture(autouse=True)
def _clean_auth_env(monkeypatch):
    for name in _AUTH_ENV:
        monkeypatch.delenv(name, raising=False)
