"""Configuration des workers Temporal."""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

QUEUE_ORCHESTRATOR = "orchestrator"
QUEUE_EXECUTOR = "executor"
QUEUE_TRACKER = "tracker"
QUEUE_MEMORY = "memory"
ALL_QUEUES = (QUEUE_ORCHESTRATOR, QUEUE_EXECUTOR, QUEUE_TRACKER, QUEUE_MEMORY)


class OrchestratorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHOREGOS_", env_file=".env", extra="ignore")

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    fakes: bool = Field(default=False, validation_alias="CHOREGOS_FAKES")
    api_url: str = "http://localhost:8000"
    internal_api_url: str = "http://localhost:8000/api/v1/internal"
    gateway_url: str = "http://localhost:4000"
    runner_image: str = "ghcr.io/vargafoundation/choregos-runner:1.0.0"
    executor_kind: str = "tekton"
    runner_namespace_pattern: str = "proj-{slug}-runners"
    object_store_url: str = "s3://choregos"
    public_url: str = "http://localhost:3000"
    log_level: str = "INFO"
    log_json: bool = True
    fx_usd_eur: float = 0.92
    history_size_threshold: int = 20_000
    heartbeat_seconds: int = 60


@lru_cache(maxsize=1)
def get_settings() -> OrchestratorSettings:
    return OrchestratorSettings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
