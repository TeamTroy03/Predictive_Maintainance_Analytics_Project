"""Settings read from environment variables or the repo-root .env file (names in .env.example).

The Databricks and Gold variables are shared with the agent. Secrets are SecretStr: they never
appear in logs or error messages. In Azure the same variables are filled from Key Vault
references, so no secret is ever in the code.
"""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=".env", extra="ignore")

    environment: Literal["dev", "prod"] = "dev"

    # Security: API keys for development, Entra ID JWT for the deployed design
    api_keys: SecretStr = SecretStr("")  # comma-separated, allows key rotation
    jwt_tenant_id: str | None = None
    jwt_audience: str | None = None
    jwt_required_role: str = "PM.Reader"

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
