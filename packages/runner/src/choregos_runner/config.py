"""Configuration du runner, lue dans l'environnement du conteneur."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunnerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHOREGOS_", extra="ignore")

    run_id: str = ""
    api_url: str = "http://localhost:8000/api/v1/internal"
    run_token: str = ""
    workspace: Path = Path("/workspace")
    tools_url: str = "http://localhost:7777/mcp"
    git_user_name: str = "choregos-bot"
    git_user_email: str = "choregos-bot@varga.foundation"
    event_batch_size: int = 25
    event_flush_seconds: float = 5.0
    dry_run: bool = Field(default=False, validation_alias="CHOREGOS_RUNNER_DRY_RUN")
    offline: bool = Field(default=False, validation_alias="CHOREGOS_RUNNER_OFFLINE")


@lru_cache(maxsize=1)
def get_settings() -> RunnerSettings:
    return RunnerSettings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
