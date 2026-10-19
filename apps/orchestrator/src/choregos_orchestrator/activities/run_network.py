# SPDX-License-Identifier: Apache-2.0
"""The network a run gets (ADR 0070): computed once, written into the run's `Permissions`.

The effective network is agent ∩ project ∩ organisation, plus the *implied* hosts, minus the deny
lists of the instance, the organisation and the project (`choregos_core.network`). The API computes
the same thing for the egress proxy (`services/network.py`); both read the same module.

Implied hosts are the repository's host and the hosts the organisation OPENED for its runners in
its egress allowlist (`org_egress_rules` with `used_by: runners`, S39-22d, ADR 0066): an opening is
the organisation's own grant to every run, so it joins the run like the repository does, and every
deny list (instance, organisation ceiling, project) still wins over it (ADR 0070, amended).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from choregos_contracts import Permissions
from choregos_core.network import EffectiveNetwork, effective_network, repo_host, unsatisfied
from temporalio.exceptions import ApplicationError

from .base import ProjectBundle


def instance_deny(settings: Any) -> list[str]:
    return [
        p.strip() for p in str(getattr(settings, "network_instance_deny", "") or "").split(",") if p.strip()
    ]


def _implied_hosts(bundle: ProjectBundle, org_hosts: Iterable[str] = ()) -> list[str]:
    host = repo_host(bundle.config.repo.url if bundle.config.repo else None)
    return list(dict.fromkeys([*([host] if host else []), *org_hosts]))


def run_network(
    bundle: ProjectBundle, agent_net: Any, settings: Any, org_hosts: Iterable[str] = ()
) -> EffectiveNetwork:
    """The network of a run of this project, with the agent's narrowing when the stage has an agent.
    `org_hosts`: what the organisation opened for its runners (`org_egress.org_runner_hosts`)."""
    ceiling = bundle.ceiling.network if bundle.ceiling else None
    return effective_network(
        bundle.policy.sandbox.network,
        agent_net,
        ceiling,
        implied_hosts=_implied_hosts(bundle, org_hosts),
        instance_deny=instance_deny(settings),
    )


def admit_network(bundle: ProjectBundle, agent: Any, settings: Any, org_hosts: Iterable[str] = ()) -> None:
    """Refuse, before any key is minted and without a retry, an agent that needs a host its project
    or the organisation does not allow: retrying would not change the policy. A host the
    organisation opened for its runners counts as allowed."""
    if agent is None or not agent.spec.network.domains:
        return
    ceiling = bundle.ceiling.network if bundle.ceiling else None
    missing = unsatisfied(
        agent.spec.network,
        bundle.policy.sandbox.network,
        ceiling,
        implied_hosts=_implied_hosts(bundle, org_hosts),
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
