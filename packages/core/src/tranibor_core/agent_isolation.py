# SPDX-License-Identifier: Apache-2.0
"""Where the agent of a step runs (ADR 0146 §9): the deployment's setting, the executors' capability, and
the one comparison that refuses a run an executor cannot isolate.

`process`: the agent is a child of the runner, in the runner's container (the historical shape).
`container`: the agent runs in a container of its own in the run's pod, started there by the launcher,
and holds none of the run's credentials. One setting for the whole deployment,
`CHOREGOS_RUNNER_AGENT_ISOLATION` (the chart's `global.runner.agentIsolation`): the API reads it for
`/readiness`, the workers for every run they start, the warm pool's template for its pods.

An executor that can start the agent's container declares `separate_agent` among its capabilities. Under
`container`, an executor without it refuses the run at `start`, before anything is sent to its backend,
and `/readiness` says the same sentence before any run: both call `agent_isolation_refusal`. Never a
quieter mode: a run asked to isolate its agent is never run with the agent beside the runner (ADR 0101).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Literal, cast

AgentIsolation = Literal["process", "container"]

#: The deployment's setting, read by the API, the workers and the runner alike.
AGENT_ISOLATION_ENV = "CHOREGOS_RUNNER_AGENT_ISOLATION"
#: The values it takes; the first is the default.
AGENT_ISOLATION_MODES: tuple[AgentIsolation, ...] = ("process", "container")
#: The capability of an executor that starts the agent in a container of its own (ADR 0146 §9).
SEPARATE_AGENT = "separate_agent"
#: The error code of a run refused for it, as `hosted_unsupported_cluster` names its own refusal.
AGENT_ISOLATION_UNAVAILABLE = "agent_isolation_unavailable"


def deployment_agent_isolation(environ: Mapping[str, str]) -> AgentIsolation:
    """The deployment's setting; `process` when it says nothing. An unknown value is refused, never
    read as `process`: a typo would otherwise turn the isolation off."""
    value = (environ.get(AGENT_ISOLATION_ENV) or "").strip() or AGENT_ISOLATION_MODES[0]
    if value not in AGENT_ISOLATION_MODES:
        raise ValueError(
            f"{AGENT_ISOLATION_ENV}={value!r}: expected one of {', '.join(AGENT_ISOLATION_MODES)} (ADR 0146)"
        )
    return cast(AgentIsolation, value)


def agent_isolation_refusal(asked: str, executor: str, capabilities: Iterable[str]) -> str | None:
    """Why `executor` cannot run a step under `asked`, or None when it can.

    ONE comparison for the executor (at `start`) and for the API (`/readiness`, before any run), so they
    cannot disagree, as `runtime_class_refusal` is for the warm pool's RuntimeClass."""
    if asked != "container" or SEPARATE_AGENT in frozenset(capabilities):
        return None
    return (
        f"{AGENT_ISOLATION_UNAVAILABLE}: the deployment runs every agent in a container of its own "
        f"({AGENT_ISOLATION_ENV}=container, ADR 0146) and the `{executor}` executor cannot start one "
        f"(it does not declare `{SEPARATE_AGENT}`); run the project on an executor that does, or set "
        "`global.runner.agentIsolation: process`"
    )
