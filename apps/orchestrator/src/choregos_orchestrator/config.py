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
    # L'URL que les AGENTS rappellent, depuis le cluster. Elle n'est pas l'URL publique :
    # un pod ne résout pas le nom d'un ingress, et le runner mourait sur « Name or service
    # not known » sans qu'aucun message ne désigne l'URL. Elle existait ici depuis le début
    # et n'était lue nulle part — les rappels prenaient `api_url`.
    internal_api_url: str = "http://localhost:8000"
    gateway_url: str = "http://localhost:4000"
    runner_image: str = "ghcr.io/vargafoundation/choregos-runner:1.0.0"
    executor_kind: str = "tekton"
    # Variables passées à CHAQUE pod d'agent (JSON dans `CHOREGOS_RUNNER_ENV`). Ce qui est
    # secret n'a rien à faire ici : `CHOREGOS_RUNNER_ENV_SECRETS` liste des secrets, que
    # l'exécuteur monte par référence.
    runner_env: dict[str, str] = {}
    runner_namespace_pattern: str = "proj-{slug}-runners"

    @property
    def callback_url(self) -> str:
        """Ce que le runner reçoit dans `callbacks.api_url`, sans le suffixe d'API interne."""
        return (self.internal_api_url or self.api_url).rstrip("/").removesuffix("/api/v1/internal")

    object_store_url: str = "s3://choregos"
    public_url: str = "http://localhost:3000"
    log_level: str = "INFO"
    log_json: bool = True
    fx_usd_eur: float = 0.92
    history_size_threshold: int = 20_000
    heartbeat_seconds: int = 60
    # Polling de secours du tracker (S3-06) : 0 désactive le rattrapage.
    reconcile_interval_seconds: int = 60


@lru_cache(maxsize=1)
def get_settings() -> OrchestratorSettings:
    return OrchestratorSettings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
