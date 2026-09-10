from __future__ import annotations

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Process configuration. Read from the environment and a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Discord
    discord_token: str = ""
    dev_guild_id: int | None = None  # when set, slash commands sync to this guild only (instant)

    # Market data
    fmp_api_key: str = ""
    fmp_base_url: str = "https://financialmodelingprep.com/stable"

    # Storage: sqlite for dev/small deployments, postgres for anything shared
    database_url: str = "sqlite+aiosqlite:///./versus.db"

    # Engine cadence
    poll_interval_s: float = 5.0
    fill_delay_s: float = 1.0
    fill_timeout_s: float = 30.0
    board_refresh_s: float = 60.0
    scheduler_tick_s: float = 15.0

    log_level: str = "INFO"

    @field_validator("dev_guild_id", mode="before")
    @classmethod
    def _blank_is_none(cls, v):
        """`DEV_GUILD_ID=` in .env arrives as an empty string; treat it as unset."""
        if isinstance(v, str) and not v.strip():
            return None
        return v


settings = Settings()
