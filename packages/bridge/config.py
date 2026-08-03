"""Bridge configuration, sourced from environment variables.

Uses pydantic-settings so values can be supplied via a `.env` file locally or
real environment variables in production/containers -- same pattern as
`packages/api/app/config.py`, trimmed to only what the bridge needs.
"""
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Database ---
    # Accepts either a plain `postgresql://` DSN or a SQLAlchemy-style
    # `postgresql+asyncpg://` DSN (the `+asyncpg` driver suffix is stripped
    # in db.py before handing the DSN to asyncpg, which does not understand it).
    DATABASE_URL: str = "postgresql+asyncpg://genmon:genmon@localhost:5432/genmon"

    # --- MQTT broker ---
    # The bridge connects with its own dedicated broker account, scoped by
    # ACL (provisioned via Mosquitto dynamic-security by the api) to
    # read-only access on the three topic filters this worker subscribes to.
    MQTT_HOST: str = "localhost"
    MQTT_PORT: int = 8883
    MQTT_TLS: bool = True
    MQTT_USERNAME: str = "genmon-bridge"
    MQTT_PASSWORD: str = "change-me"
    MQTT_TOPIC_PREFIX: str = "genmon"

    @field_validator("MQTT_TLS", mode="before")
    @classmethod
    def _parse_bool(cls, v):
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return v


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
