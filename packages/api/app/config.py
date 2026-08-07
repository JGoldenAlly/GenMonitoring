"""Application configuration, sourced from environment variables.

Uses pydantic-settings so values can be supplied via a `.env` file locally or
real environment variables in production/containers.
"""
import logging
from functools import lru_cache
from typing import List

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database ---
    DATABASE_URL: str = "postgresql+asyncpg://genmon:genmon@localhost:5432/genmon"

    # --- JWT / auth ---
    JWT_SECRET: str = "change-me-in-production"
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # --- MQTT broker (api's own pub/sub credentials, for publishing
    # genmon/{device_key}/cmd) ---
    MQTT_HOST: str = "localhost"
    MQTT_PORT: int = 1883
    MQTT_TLS: bool = False
    MQTT_USERNAME: str = "genmon-api"
    MQTT_PASSWORD: str = "change-me"

    # --- EMQX HTTP Management API (separate from the MQTT pub/sub
    # credentials above -- this is what app/services/emqx_admin.py uses to
    # provision/revoke per-device MQTT credentials and ACL rules) ---
    EMQX_API_URL: str = "http://localhost:18083"
    EMQX_API_KEY: str = "change-me"
    EMQX_API_SECRET: str = "change-me"

    # --- CORS ---
    CORS_ORIGINS: str = "http://localhost:3000"

    # --- Server ---
    PORT: int = 8000

    # --- Misc / device provisioning ---
    AGENT_SCRIPT_PATH: str = "/app/agent/genmon_agent.py"
    TARGET_AGENT_VERSION: str = "1.0.0"

    # --- Command / session defaults ---
    DEFAULT_COMMAND_TTL_SECONDS: int = 300
    MAX_COMMAND_TTL_SECONDS: int = 1800
    DEFAULT_RUN_SESSION_MINUTES: int = 30

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @field_validator("MQTT_TLS", mode="before")
    @classmethod
    def _parse_bool(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return v

    @field_validator("EMQX_API_URL", mode="after")
    @classmethod
    def _require_emqx_url_scheme(cls, v: str) -> str:
        # httpx.AsyncClient(base_url=...) raises deep inside the request
        # path -- not at startup -- if this is missing a scheme (e.g. a
        # bare "10.20.1.63:18083", the natural thing to type after testing
        # the EMQX API by hand with curl). Normalize it here instead of
        # letting every device-claim attempt crash with a cryptic
        # "UnsupportedProtocol" traceback that gives no hint what to fix.
        if v and "://" not in v:
            logger.warning(
                "EMQX_API_URL=%r has no http(s):// scheme; assuming http://. "
                "Set it explicitly (e.g. http://mqtt.allyoperations.com:18083) to silence this.",
                v,
            )
            return f"http://{v}"
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
