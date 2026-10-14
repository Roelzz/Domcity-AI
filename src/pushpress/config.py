"""Runtime configuration loaded from environment variables and a local ``.env``."""

from __future__ import annotations

import os

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # PushPress login. The client POSTs email+password to /v2/auth/login, caches
    # the JWT in SQLite, and re-logs in whenever the cached token is near expiry.
    pushpress_email: str = ""
    pushpress_password: str = ""

    # Gym's local zone. PushPress serialises class times with a misleading
    # +00:00 suffix — the digits are the local wall-clock, not UTC.
    tz: str = "Europe/Amsterdam"

    # Where the JWT is persisted. In Coolify point this at a mounted volume.
    db_path: str = "./data/pushpress.db"

    log_level: str = "INFO"

    # MCP server (streamable HTTP). ``mcp_token`` is an optional static bearer
    # token that gates the HTTP endpoint. Leave empty to disable auth.
    mcp_token: str = ""
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000

    @property
    def configured(self) -> bool:
        return bool(self.pushpress_email and self.pushpress_password)


settings = Settings()
os.environ.setdefault("TZ", settings.tz)
