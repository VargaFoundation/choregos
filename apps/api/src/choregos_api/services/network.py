# SPDX-License-Identifier: Apache-2.0
"""Network access (ADR 0070): the network of a run, the checks at publication, the decisions journal.

The effective network of a run is agent ∩ project ∩ organisation, plus the host of the project's
repository and the hosts the organisation opened for its runners (ADR 0066), minus the deny lists of
the instance, the organisation and the project. It is computed here, once, by `choregos_core.network`,
for the egress proxy (`GET /internal/runs/{id}/network`), for the console
(`GET /projects/{id}/network`) and, in the orchestrator, for the run's `Permissions`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from choregos_contracts import EventType
from choregos_core import aware, utcnow
from choregos_core.network import EffectiveNetwork, effective_network, repo_host, unsatisfied
from choregos_core.policy_ceiling import PolicyCeiling
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import Agent, AgentVersion, NetworkDecision, Organization, Project, Run
from ..errors import unprocessable
from ..schemas.agents import AgentNetwork, AgentSpec
from ..schemas.network import NetworkDecisionBatch, RunNetwork
from .definitions import ceiling_of, effective_policy
from .egress import org_runner_hosts
from .evenements import persist_event


def instance_deny() -> list[str]:
    """The hosts this deployment refuses to every run (`CHOREGOS_NETWORK_INSTANCE_DENY`)."""
    return [p.strip() for p in get_settings().network_instance_deny.split(",") if p.strip()]


async def check_agent_network(session: AsyncSession, org_id: str, spec: AgentSpec) -> None:
    """Refuse (422) a version that asks for a host the organisation does not allow.

    At publication only the organisation is known: the project's own allowlist is checked when a
    run is admitted (the orchestrator refuses it, non-retryable) and shown by the console.
    """
    if not spec.network.domains:
        return
    ceiling = ceiling_of(await session.get(Organization, org_id))
    problems = unsatisfied(
        spec.network, None, ceiling.network if ceiling else None, instance_deny=instance_deny()
    )
    if problems:
        raise unprocessable(
            "the agent's network asks for hosts the organisation does not allow",
            [{"loc": ["spec", "network", "domains"], "msg": line} for line in problems],
        )


def project_repo_host(project: Project) -> str | None:
    repo = ((project.config or {}).get("repo") or {}).get("url")
    return repo_host(repo if isinstance(repo, str) else None)


async def project_effective_network(
    session: AsyncSession,
    project: Project,
    agent_net: AgentNetwork | None = None,
) -> tuple[EffectiveNetwork, PolicyCeiling | None]:
    """The network a run of this project gets (with the agent's narrowing, when it has one)."""
    policy = await effective_policy(session, project.id)
    ceiling = ceiling_of(await session.get(Organization, project.org_id))
    # The repository's host, and what the organisation opened for its runners (S39-22d): the same
    # implied hosts the orchestrator writes into the run's permissions (ADR 0070, amended).
    implied = [h for h in [project_repo_host(project)] if h] + await org_runner_hosts(session, project.org_id)
    effective = effective_network(
        policy.sandbox.network,
        agent_net,
        ceiling.network if ceiling else None,
        implied_hosts=implied,
        instance_deny=instance_deny(),
    )
    return effective, ceiling


async def _agent_network_of(session: AsyncSession, run: Run) -> AgentNetwork | None:
    """The network the run's agent version declares. A trial (a draft that is no registry version)
    and a job step have no version: the orchestrator wrote what applies into the run's permissions."""
    if run.agent_slug and run.agent_version:
        spec = (
            await session.execute(
                select(AgentVersion.spec)
                .join(Agent, Agent.id == AgentVersion.agent_id)
                .where(Agent.slug == run.agent_slug, AgentVersion.version == run.agent_version)
            )
        ).scalar_one_or_none()
        if spec is not None:
            return AgentNetwork.model_validate(spec.get("network") or {})
    permissions = (run.stage_input or {}).get("permissions") or {}
    if permissions.get("egress") == "listed":
        return AgentNetwork(
            domains=[str(d) for d in permissions.get("allow_domains") or []],
            builtin_web_tools=False if permissions.get("builtin_web_tools") is False else None,
        )
    return None


async def run_network(session: AsyncSession, run: Run) -> RunNetwork:
    project = await session.get(Project, run.project_id)
    assert project is not None
    organisation = await session.get(Organization, project.org_id)
    effective, _ = await project_effective_network(session, project, await _agent_network_of(session, run))
    return RunNetwork(
        run_id=run.id,
        allow=effective.allow,
        deny=effective.deny,
        implied=effective.implied,
        per_host_per_minute=effective.per_host_per_minute,
        per_run_per_minute=effective.per_run_per_minute,
        org_per_host_per_minute=effective.org_per_host_per_minute,
        builtin_web_tools=effective.builtin_web_tools,
        listed=True,
        org=organisation.slug if organisation else "",
        project=project.slug,
        agent=run.agent_slug,
    )


async def record_decisions(
    session: AsyncSession, run: Run, project: Project, batch: NetworkDecisionBatch
) -> int:
    """Store what the proxy or the runner reports, as CUMULATIVE totals per (layer, host, port,
    decision, reason): a replayed batch rewrites the same totals and never doubles them. The first
    refusal of a host for a run is also journaled on the work item's timeline."""
    now = utcnow()
    for item in batch.decisions:
        host = item.host.strip().lower().rstrip(".")
        row = (
            await session.execute(
                select(NetworkDecision).where(
                    NetworkDecision.run_id == run.id,
                    NetworkDecision.layer == item.layer,
                    NetworkDecision.host == host,
                    NetworkDecision.port == item.port,
                    NetworkDecision.decision == item.decision,
                    NetworkDecision.reason == item.reason,
                )
            )
        ).scalar_one_or_none()
        first: datetime = item.first_at or now
        last: datetime = item.last_at or now
        if row is None:
            if item.decision == "denied":
                await _journal_first_denial(session, run, project, host, item.reason, item.layer)
            session.add(
                NetworkDecision(
                    id=str(uuid.uuid4()),
                    org_id=project.org_id,
                    project_id=project.id,
                    run_id=run.id,
                    agent_slug=run.agent_slug,
                    layer=item.layer,
                    host=host,
                    port=item.port,
                    decision=item.decision,
                    reason=item.reason,
                    count=item.count,
                    bytes_up=item.bytes_up,
                    bytes_down=item.bytes_down,
                    first_at=first,
                    last_at=last,
                )
            )
            await session.flush()
        else:
            row.count = item.count
            row.bytes_up = item.bytes_up
            row.bytes_down = item.bytes_down
            row.last_at = max(aware(last) or now, aware(row.last_at) or now)
    return len(batch.decisions)


async def _journal_first_denial(
    session: AsyncSession, run: Run, project: Project, host: str, reason: str, layer: str
) -> None:
    already = (
        await session.execute(
            select(NetworkDecision.id)
            .where(
                NetworkDecision.run_id == run.id,
                NetworkDecision.host == host,
                NetworkDecision.decision == "denied",
            )
            .limit(1)
        )
    ).first()
    if already is not None:
        return
    await persist_event(
        session,
        EventType.NETWORK_DENIED,
        project_id=project.id,
        work_item_id=run.work_item_id,
        project_slug=project.slug,
        subject=run.id,
        run_id=run.id,
        host=host,
        reason=reason,
        layer=layer,
    )
