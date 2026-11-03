# SPDX-License-Identifier: Apache-2.0
"""An agent that asks for memory in a project whose policy disables it: say so, never refuse (S51-01).

The policy's `memory.enabled: false` is authoritative at run time (the orchestrator binds no memory and
announces no memory server). Publishing a workflow that names such an agent, or a policy that turns
memory off under such a workflow, is still accepted — memory never blocks (ADR 0007) — but the
platform says it, through the channels warnings already use: the project-scoped validation the editor
calls (`WorkflowValidation.warnings`) and the `policy.memory_disabled` log line at publication.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from choregos_contracts import AgentActor, Workflow
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Agent, AgentVersion, WorkflowDef
from ..logging import get_logger

CODE = "agent.memory_disabled_by_policy"


def _named_actors(workflow: Workflow) -> list[tuple[str, str, int | None]]:
    """`(actor id, agent slug, pinned version or None)` for every actor naming a registry agent."""
    named = []
    for actor_id, actor in workflow.actors.items():
        if isinstance(actor, AgentActor) and actor.agent:
            slug, _, pin = str(actor.agent).partition("@")
            named.append((actor_id, slug, int(pin) if pin.isdigit() else None))
    return named


async def _bindings_of(session: AsyncSession, org_id: str, slug: str, pinned: int | None) -> list[str]:
    """The labels of the memories the version a run would get binds: the pinned one, else the stable
    channel, else the latest. Empty for an unknown agent (the validator's and the run's business)."""
    from ..schemas.agents import AgentSpec

    agent = (
        await session.execute(select(Agent).where(Agent.org_id == org_id, Agent.slug == slug))
    ).scalar_one_or_none()
    if agent is None:
        return []
    version = pinned or agent.stable_version
    if version is None:
        version = (
            await session.execute(
                select(func.max(AgentVersion.version)).where(AgentVersion.agent_id == agent.id)
            )
        ).scalar()
    spec = (
        await session.execute(
            select(AgentVersion.spec).where(
                AgentVersion.agent_id == agent.id, AgentVersion.version == version
            )
        )
    ).scalar_one_or_none()
    if spec is None:
        return []
    try:
        return [f"{b.scope}:{b.slug}" for b in AgentSpec.model_validate(spec).effective_memories()]
    except ValidationError:  # a stored spec the current schema refuses is not this warning's business
        return []


async def active_workflows_of(session: AsyncSession, project_id: str) -> list[Workflow]:
    """Every active workflow of a project (one per name, ADR 0031)."""
    from .definitions import workflow_model

    rows = await session.execute(
        select(WorkflowDef).where(WorkflowDef.project_id == project_id, WorkflowDef.is_active.is_(True))
    )
    return [workflow_model(row) for row in rows.scalars()]


async def memory_disabled_warnings(
    session: AsyncSession, org_id: str, workflows: Iterable[Workflow], *, memory_enabled: bool
) -> list[dict[str, Any]]:
    """One `WorkflowIssue`-shaped warning per actor whose agent binds memory, when the policy disables
    it; empty when the policy enables memory."""
    if memory_enabled:
        return []
    issues: list[dict[str, Any]] = []
    for workflow in workflows:
        for actor_id, slug, pinned in _named_actors(workflow):
            bound = await _bindings_of(session, org_id, slug, pinned)
            if not bound:
                continue
            issues.append(
                {
                    "code": CODE,
                    "message": (
                        f"agent `{slug}` asks for memory ({', '.join(bound)}) but the project's policy "
                        "disables it (memory.enabled: false): it will get none"
                    ),
                    "path": f"/actors/{actor_id}/agent",
                    "workflow": workflow.metadata.name,
                    "agent": slug,
                }
            )
    return issues


def log_memory_disabled_warnings(issues: list[dict[str, Any]], project: str) -> None:
    """One `policy.memory_disabled` warning line per issue (the channel of `policy.not_enforced`)."""
    logger = get_logger("choregos.policy")  # per call: `structlog.testing.capture_logs` must see it
    for issue in issues:
        logger.warning(
            "policy.memory_disabled",
            project=project,
            workflow=issue["workflow"],
            agent=issue["agent"],
            path=issue["path"],
            message=issue["message"],
        )
