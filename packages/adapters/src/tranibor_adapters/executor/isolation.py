# SPDX-License-Identifier: Apache-2.0
"""The executors' side of ADR 0146 §9: the refusal at `start`, and what each kind declares.

`refuse_unless_separate` is the first thing every executor's `start` does: under `container`, an
executor that does not declare `separate_agent` raises before it writes a Secret, a claim, a Job or a
container. `declared_capabilities` reads what an executor of a kind declares in this deployment from its
class, without building it: the API judges `/readiness` with it, and it holds no cluster credential.
"""

from __future__ import annotations

from collections.abc import Iterable

from tranibor_core.agent_isolation import agent_isolation_refusal
from tranibor_core.domain import StageJobSpec

from ..errors import AgentIsolationRefused


def refuse_unless_separate(executor: str, capabilities: Iterable[str], spec: StageJobSpec) -> None:
    """Raises `AgentIsolationRefused` when the run asks for the agent's own container and the executor
    cannot start one."""
    refusal = agent_isolation_refusal(spec.agent_isolation, executor, capabilities)
    if refusal is not None:
        raise AgentIsolationRefused(refusal)


def declared_capabilities(kind: str, *, agent_isolation: str) -> frozenset[str]:
    """What an executor of `kind` declares in a deployment whose setting is `agent_isolation`, read from
    its class. The warm pool's depends on its template, which the chart renders from the same setting.
    An unknown kind declares nothing."""
    from .aca import AcaExecutor
    from .agent_sandbox import AgentSandboxExecutor
    from .k8s_job import KubernetesJobExecutor
    from .local_docker import LocalDockerExecutor
    from .tekton import TektonExecutor

    if kind == "agent_sandbox":
        return AgentSandboxExecutor.pool_capabilities(agent_isolation)
    classes = {
        "k8s_job": KubernetesJobExecutor,
        "local_docker": LocalDockerExecutor,
        "tekton": TektonExecutor,
        "aca": AcaExecutor,
    }
    found = classes.get(kind)
    return frozenset(found.capabilities) if found is not None else frozenset()
