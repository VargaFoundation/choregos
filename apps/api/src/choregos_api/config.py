"""Configuration de l'API (12-factor, préfixe `CHOREGOS_`)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHOREGOS_", env_file=".env", extra="ignore")

    env: Literal["dev", "staging", "prod", "test"] = "dev"
    # Port d'écoute. En conteneur, 8000 ; sur une machine de développement, 8000 est
    # souvent déjà pris, et `choregos-api` sortait sur « Address already in use » sans
    # qu'on puisse en changer autrement qu'en éditant le code.
    port: int = 8000
    debug: bool = False
    fakes: bool = Field(default=False, validation_alias="CHOREGOS_FAKES")

    # Base de données
    database_url: str = "sqlite+aiosqlite:///./choregos.db"
    db_echo: bool = False
    db_pool_size: int = 10

    # Identité
    oidc_issuer: str = "http://localhost:8080/realms/choregos"
    oidc_client_id: str = "choregos"
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid profile email groups"
    session_secret: str = "dev-session-secret-change-me"
    session_cookie: str = "choregos_session"
    session_max_age_s: int = 8 * 3600
    dev_login_enabled: bool = True

    # JWT de run (ES256) — clés PEM ; générées à la volée en dev
    run_token_private_key: str = ""
    run_token_public_key: str = ""
    run_token_issuer: str = "choregos-api"
    run_token_audience: str = "internal"

    # Services
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "orchestrator"
    gateway_url: str = "http://localhost:4000"
    gateway_master_key: str = ""
    memory_url: str = "http://localhost:8432"
    object_store_url: str = "s3://choregos"
    public_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000"

    # Webhooks
    github_webhook_secret: str = ""
    github_app_id: str = ""
    github_app_private_key: str = ""
    generic_webhook_secret: str = "dev-webhook-secret"

    # Divers
    cors_origins: str = "http://localhost:3000"
    rate_limit_per_minute: int = 600
    log_level: str = "INFO"
    log_json: bool = True
    fx_usd_eur: float = 0.92

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
