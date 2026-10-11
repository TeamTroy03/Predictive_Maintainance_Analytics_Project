"""Unit tests never read the developer's .env: a real JWT_SECRET or AUTH_USERS in it would change their outcome.
(Integration tests are outside this folder and keep using the real .env.)"""

from app.api.config import Settings

Settings.model_config["env_file"] = None