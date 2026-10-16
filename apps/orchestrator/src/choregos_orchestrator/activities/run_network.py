# SPDX-License-Identifier: Apache-2.0
"""The network a run gets (ADR 0070): computed once, written into the run's `Permissions`.

The effective network is agent ∩ project ∩ organisation, plus the repository host, minus the deny
lists of the instance, the organisation and the project (`choregos_core.network`). The API computes
the same thing for the egress proxy (`services/network.py`); both read the same module.
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import Permissions
from choregos_core.network import EffectiveNetwork, effective_network, repo_host, unsatisfied
from temporalio.exceptions import ApplicationError

from .base import ProjectBundle


def instance_deny(settings: Any) -> list[str]:
    return [
        p.strip() for p in str(getattr(settings, "network_instance_deny", "") or "").split(",") if p.strip()
    ]


def _implied_hosts(bundle: ProjectBundle) -> list[str]:
    host = repo_host(bundle.config.repo.url if bundle.config.repo else None)
    return [host] if host else []


def run_network(bundle: ProjectBundle, agent_net: Any, settings: Any) -> EffectiveNetwork:
    """The network of a run of this project, with the agent's narrowing when the stage has an agent."""
    ceiling = bundle.ceiling.network if bundle.ceiling else None
    return effective_network(
        bundle.policy.sandbox.network,
        agent_net,
        ceiling,
        implied_hosts=_implied_hosts(bundle),
        instance_deny=instance_deny(settings),
    )


def admit_network(bundle: ProjectBundle, agent: Any, settings: Any) -> None:
    """Refuse, before any key is minted and without a retry, an agent that needs a host its project
    or the organisation does not allow: retrying would not change the policy."""
    if agent is None or not agent.spec.network.domains:
        return
    ceiling = bundle.ceiling.network if bundle.ceiling else None
    missing = unsatisfied(
        agent.spec.network,
        bundle.policy.sandbox.network,
        ceiling,
        implied_hosts=_implied_hosts(bundle),
        instance_deny=instance_deny(settings),
    )
    if missing:
        raise ApplicationError(
            f"run refused before it started: the agent `{agent.agent.slug}` needs hosts the project "
            f"`{bundle.slug}` or the organisation does not allow (project policy "
            f"sandbox.network.allow_domains, organisation policy ceiling): " + "; ".join(missing),
            type="AgentIndisponible",
            non_retryable=True,
        )


def network_permissions(effective: EffectiveNetwork, **rest: Any) -> Permissions:
    """The run's `Permissions`: the list is complete (`listed`), so an empty one means no internet."""
    return Permissions(
        allow_domains=effective.reachable,
        deny_domains=effective.deny,
        egress="listed",
        builtin_web_tools=effective.builtin_web_tools,
        **rest,
    )
