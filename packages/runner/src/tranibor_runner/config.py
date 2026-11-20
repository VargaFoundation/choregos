# SPDX-License-Identifier: Apache-2.0
"""Runner configuration, read from the container's environment."""

from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from tranibor_core.publishing import PUBLISHING

#: The prefix of the run's own variables: `TRANIBOR_RUN_TOKEN`, `TRANIBOR_RUN_REMOTE_TOKEN` and
#: `TRANIBOR_RUN_GATEWAY_KEY`, which `k8s_job`, ACA, local Docker and Tekton put in the runner's
#: environment from the run's Secret (#519, #523, ADR 0062). `agent_sandbox` keeps the token in
#: memory and puts the other two in the environment its assignment carries.
RUN_VARIABLE_PREFIX = "TRANIBOR_RUN_"
#: The former spelling (ADR 0176 R4): the executors put both in the runner's environment during the window,
#: and both are credentials.
LEGACY_RUN_VARIABLE_PREFIX = "CHOREGOS_RUN_"  # compat-ok
#: The run's variables that are not credentials: what a process the runner starts may inherit.
#: Every other `TRANIBOR_RUN_*` variable is taken for a credential, so that a new one is kept out
#: of the agent's reach until somebody decides otherwise here.
INHERITABLE_RUN_VARIABLES = frozenset({"TRANIBOR_RUN_ID", "CHOREGOS_RUN_ID"})  # compat-ok


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
        if not name.startswith((RUN_VARIABLE_PREFIX, LEGACY_RUN_VARIABLE_PREFIX))
        or name in INHERITABLE_RUN_VARIABLES
    }


class RunnerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRANIBOR_", extra="ignore")

    run_id: str = ""
    api_url: str = "http://localhost:8000/api/v1/internal"
    run_token: str = ""
    #: The run's gateway key, from the run's Secret, next to its token (#523). Empty when the
    #: orchestrator is older than #523: the runner then uses the key of its input
    #: (`GET /internal/runs/{id}/input`), as before — kept for one release.
    gateway_key: str = Field(default="", validation_alias="TRANIBOR_RUN_GATEWAY_KEY")
    #: The access token for the run's remote agent (ADR 0062), from the run's Secret, never from the
    #: StageInput: the orchestrator exchanged the credential's refresh token for it, short-lived.
    remote_token: str = Field(default="", validation_alias="TRANIBOR_RUN_REMOTE_TOKEN")
    #: How often a remote task that does not stream is asked for its state.
    remote_poll_seconds: float = Field(default=5.0, gt=0, validation_alias="TRANIBOR_REMOTE_POLL_SECONDS")
    workspace: Path = Path("/workspace")
    #: Where the runner keeps the git directory of the run, outside the tree the agent writes
    #: (ADR 0146 §5). Empty: `/var/tmp/choregos-git`, which is not a write root of the confinement.
    git_dir_root: Path | None = Field(default=None, validation_alias="TRANIBOR_RUNNER_GIT_DIR_ROOT")
    #: The copy of the working tree a hosted container works in (S41-18): a volume the two
    #: containers share, never the runner's checkout.
    hosted_workspace: Path = Path("/hosted-workspace")
    tools_url: str = "http://localhost:7777/mcp"
    git_user_name: str = "choregos-bot"
    git_user_email: str = PUBLISHING.commit_email
    event_batch_size: int = 25
    event_flush_seconds: float = 5.0
    dry_run: bool = Field(default=False, validation_alias="TRANIBOR_RUNNER_DRY_RUN")
    offline: bool = Field(default=False, validation_alias="TRANIBOR_RUNNER_OFFLINE")
    #: The same tool with the same arguments this many times in a row stops the agent (`reason=loop`,
    #: S27-07). 0 or 1 disables the check.
    loop_threshold: int = Field(default=5, ge=0, validation_alias="TRANIBOR_RUNNER_LOOP_THRESHOLD")
    #: How the kernel confines the agent's process (ADR 0094): `best_effort` applies what the kernel
    #: supports and journals what it could not, `enforce` refuses to start the agent without it.
    confinement: Literal["off", "best_effort", "enforce"] = Field(
        default="best_effort", validation_alias="TRANIBOR_RUNNER_CONFINEMENT"
    )
    #: Where the agent runs (ADR 0146): `process`, a child of the runner in its container (the
    #: historical shape), or `container`, in a container of the pod of its own, started by the
    #: launcher there. Under `container` the runner never falls back to a child: with no launcher it
    #: exits with `AGENT_ISOLATION_UNAVAILABLE`.
    agent_isolation: Literal["process", "container"] = Field(
        default="process", validation_alias="TRANIBOR_RUNNER_AGENT_ISOLATION"
    )
    #: The launcher's address: the pod's loopback, the namespace the two containers share.
    launcher_host: str = Field(default="127.0.0.1", validation_alias="TRANIBOR_RUNNER_LAUNCHER_HOST")
    launcher_port: int = Field(default=8766, validation_alias="TRANIBOR_RUNNER_LAUNCHER_PORT")
    #: How long the runner waits for the launcher before it ends the run.
    launcher_wait_seconds: float = Field(default=60.0, validation_alias="TRANIBOR_RUNNER_LAUNCHER_WAIT")
    #: The egress gate's report (S80-07f), written by the pod's first init container before anything
    #: else ran: how long the pod's egress policy took to be in force. Unset: no gate in this pod.
    egress_gate_report: Path | None = Field(default=None, validation_alias="TRANIBOR_EGRESS_GATE_REPORT")
    #: The headroom the runner starts when a run asks for compression (ADR 0055).
    headroom_command: str = "headroom"


@lru_cache(maxsize=1)
def get_settings() -> RunnerSettings:
    return RunnerSettings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
