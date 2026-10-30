"""Helpers for the tests of an actor that names a registry agent (ADR 0033, S18-02)."""

from __future__ import annotations

from typing import Any

from ..conftest import Fixture

AGENT_INSTRUCTIONS = (
    "Tu coordonnes l'arrivée décrite dans « {{ ticket.title }} » : comptes, groupes, matériel."  # lang: fr-ok
)


async def install_agent(
    setup: Fixture,
    *,
    slug: str = "coordinateur",
    status: str = "active",
    instructions: str = AGENT_INSTRUCTIONS,
    lifecycle: str = "active",
    pin: dict[str, Any] | None = None,
    scope: str = "org",
    project: str | None = None,
) -> None:
    from choregos_api.db.models import Agent, AgentVersion, Project, ProjectAgent
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project_row = await session.get(Project, setup.project_id)
        assert project_row is not None
        project_id = None
        if project == "this":
            project_id = project_row.id
        elif project == "other":
            elsewhere = Project(org_id=project_row.org_id, slug="elsewhere", name="Elsewhere")
            session.add(elsewhere)
            await session.flush()
            project_id = elsewhere.id
        agent = Agent(
            org_id=project_row.org_id,
            slug=slug,
            kind="internal",
            display_name=slug,
            status=status,
            lifecycle=lifecycle,
            scope=scope,
            project_id=project_id,
        )
        session.add(agent)
        await session.flush()
        spec = {
            "instructions": instructions,
            "model": "profile:standard",
            "limits": {"max_turns": 7, "max_minutes": 20},
            "budget": {"run_usd": 0.5, "daily_usd": 5.0},
            "skills": [],
            "mcp_servers": [],
        }
        session.add(
            AgentVersion(
                agent_id=agent.id, org_id=project_row.org_id, version=1, spec=spec, checksum="sha256:v1"
            )
        )
        if pin is not None:
            session.add(
                ProjectAgent(
                    project_id=setup.project_id,
                    org_id=project_row.org_id,
                    agent_id=agent.id,
                    version=1,
                    overrides=pin,
                )
            )


def agent_stage_plan(setup: Fixture, **extra: Any) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": "t-implement",
        "role": "implement",
        "from_state": "ready",
        "to_state": "done",
        "actor": "dev",
        "attempt": 1,
        **extra,
    }
