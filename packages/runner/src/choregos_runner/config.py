# SPDX-License-Identifier: Apache-2.0
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
    #: The run's gateway key, from the run's Secret, next to its token (#523). Empty when the
    #: orchestrator is older than #523: the runner then uses the key of its input
    #: (`GET /internal/runs/{id}/input`), as before — kept for one release.
    gateway_key: str = Field(default="", validation_alias="CHOREGOS_RUN_GATEWAY_KEY")
    #: The access token for the run's remote agent (ADR 0062), from the run's Secret, never from the
    #: StageInput: the orchestrator exchanged the credential's refresh token for it, short-lived.
    remote_token: str = Field(default="", validation_alias="CHOREGOS_RUN_REMOTE_TOKEN")
    #: How often a remote task that does not stream is asked for its state.
    remote_poll_seconds: float = Field(default=5.0, gt=0, validation_alias="CHOREGOS_REMOTE_POLL_SECONDS")
    workspace: Path = Path("/workspace")
    #: The copy of the working tree a hosted container works in (S41-18): a volume the two
    #: containers share, never the runner's checkout.
    hosted_workspace: Path = Path("/hosted-workspace")
    tools_url: str = "http://localhost:7777/mcp"
    git_user_name: str = "choregos-bot"
    git_user_email: str = "choregos-bot@varga.foundation"
    event_batch_size: int = 25
    event_flush_seconds: float = 5.0
    dry_run: bool = Field(default=False, validation_alias="CHOREGOS_RUNNER_DRY_RUN")
    offline: bool = Field(default=False, validation_alias="CHOREGOS_RUNNER_OFFLINE")
    #: The same tool with the same arguments this many times in a row stops the agent (`reason=loop`,
    #: S27-07). 0 or 1 disables the check.
    loop_threshold: int = Field(default=5, ge=0, validation_alias="CHOREGOS_RUNNER_LOOP_THRESHOLD")
    #: The headroom the runner starts when a run asks for compression (ADR 0055).
    headroom_command: str = "headroom"


@lru_cache(maxsize=1)
def get_settings() -> RunnerSettings:
    return RunnerSettings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
