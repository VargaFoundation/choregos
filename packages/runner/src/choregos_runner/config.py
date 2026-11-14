# SPDX-License-Identifier: Apache-2.0
"""Runner configuration, read from the container's environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Literal

from choregos_core.publishing import PUBLISHING
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

#: The prefix of the run's own variables: `CHOREGOS_RUN_TOKEN`, `CHOREGOS_RUN_REMOTE_TOKEN` and
#: `CHOREGOS_RUN_GATEWAY_KEY`, which `k8s_job`, ACA, local Docker and Tekton put in the runner's
#: environment from the run's Secret (#519, #523, ADR 0062). `agent_sandbox` keeps the token in
#: memory and puts the other two in the environment its assignment carries.
RUN_VARIABLE_PREFIX = "CHOREGOS_RUN_"
#: The run's variables that are not credentials: what a process the runner starts may inherit.
#: Every other `CHOREGOS_RUN_*` variable is taken for a credential, so that a new one is kept out
#: of the agent's reach until somebody decides otherwise here.
INHERITABLE_RUN_VARIABLES = frozenset({"CHOREGOS_RUN_ID"})


def agent_base_environment(environ: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment every process the runner starts begins from: the runner's own
    (`os.environ` by default), without the run's credentials (ADR 0115).

    The agent, the commands it can shape (the project's checks run the code it wrote, and their
    output comes back to it) and the runner's git all start from here. The runner keeps the
    credentials in its settings, read before anything starts, and hands one on by name only where
    it means to: a job's run token (ADR 0056), the gateway key under the backend's own variable
    (`Backend.model_env`). `os.environ` is left as it is: the settings may be read again
    (`reset_settings_cache`), and removing a variable would not erase it from
    `/proc/<runner>/environ`, which the confinement keeps out of the agent's reach (ADR 0094).
    """
    base = os.environ if environ is None else environ
    return {
        name: value
        for name, value in base.items()
        if not name.startswith(RUN_VARIABLE_PREFIX) or name in INHERITABLE_RUN_VARIABLES
    }


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
    git_user_email: str = PUBLISHING.commit_email
    event_batch_size: int = 25
    event_flush_seconds: float = 5.0
    dry_run: bool = Field(default=False, validation_alias="CHOREGOS_RUNNER_DRY_RUN")
    offline: bool = Field(default=False, validation_alias="CHOREGOS_RUNNER_OFFLINE")
    #: The same tool with the same arguments this many times in a row stops the agent (`reason=loop`,
    #: S27-07). 0 or 1 disables the check.
    loop_threshold: int = Field(default=5, ge=0, validation_alias="CHOREGOS_RUNNER_LOOP_THRESHOLD")
    #: How the kernel confines the agent's process (ADR 0094): `best_effort` applies what the kernel
    #: supports and journals what it could not, `enforce` refuses to start the agent without it.
    confinement: Literal["off", "best_effort", "enforce"] = Field(
        default="best_effort", validation_alias="CHOREGOS_RUNNER_CONFINEMENT"
    )
    #: The headroom the runner starts when a run asks for compression (ADR 0055).
    headroom_command: str = "headroom"


@lru_cache(maxsize=1)
def get_settings() -> RunnerSettings:
    return RunnerSettings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
