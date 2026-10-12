"""Settings read from environment variables or the repo-root .env file (names in .env.example).

The Databricks and Gold variables are shared with the agent. Secrets are SecretStr: they never
appear in logs or error messages. In Azure the same variables are filled from Key Vault
references, so no secret is ever in the code.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


MIN_SECRET_LENGTH = 32
REPO_ROOT = Path(__file__).resolve().parents[3]  # src/app/core/config.py -> repo root, whatever the working directory


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=REPO_ROOT / ".env", extra="ignore")

    environment: Literal["dev", "prod"] = "dev"

    # Security: API keys for development, Entra ID JWT for the deployed design
    api_keys: SecretStr = SecretStr("")  # comma-separated, allows key rotation
    jwt_tenant_id: str | None = None
    jwt_audience: str | None = None
    jwt_required_role: str = "PM.Reader"

    # Cookie login for the (closed) website: our own JWT in an HttpOnly cookie. Users are added by hand:
    # AUTH_USERS="email=scrypt$salt$hash;email2=..." (made by `python -m app.api.hash_password <email>`).
    jwt_secret: SecretStr = SecretStr("")  # signs the session token, at least 32 characters
    auth_users: SecretStr = SecretStr("")
    session_minutes: int = 60
    cookie_secure: bool = True  # HTTPS only; False only for a browser that refuses Secure cookies on localhost
    frontend_dist: str | None = None  # built React app; set by the Docker image so one container serves site + API

    # Databricks SQL warehouse that serves the Gold tables
    databricks_host: str = ""  # e.g. adb-1234567890123456.7.azuredatabricks.net
    databricks_http_path: str = ""  # e.g. /sql/1.0/warehouses/abcdef1234567890
    databricks_token: SecretStr = SecretStr("")
    gold_catalog: str = "data2026acc123456"
    gold_schema: str = "team3"
    gold_table_prefix: str = "test_"  # tables are named <prefix>gold_<name>, same as the agent
    query_timeout_seconds: int = 30
    query_retries: int = 2

    def api_key_list(self) -> list[str]:
        return [k.strip() for k in self.api_keys.get_secret_value().split(",") if k.strip()]

    @property
    def jwt_enabled(self) -> bool:
        return bool(self.jwt_tenant_id and self.jwt_audience)

    def users(self) -> dict[str, str]:
        """email (lower case) -> password hash, from AUTH_USERS."""
        entries = (e.split("=", 1) for e in self.auth_users.get_secret_value().split(";") if "=" in e)
        return {email.strip().lower(): hashed.strip() for email, hashed in entries}

    @property
    def cookie_auth_enabled(self) -> bool:
        """Never on with a short or empty secret: anyone could then forge a session token."""
        return len(self.jwt_secret.get_secret_value()) >= MIN_SECRET_LENGTH and bool(self.users())

    def check_production(self) -> None:
        if self.environment == "prod" and not (self.cookie_secure and self.cookie_auth_enabled):
            raise RuntimeError(
                "Unsafe auth for prod: COOKIE_SECURE must be true, JWT_SECRET at least "
                f"{MIN_SECRET_LENGTH} characters and AUTH_USERS not empty"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()