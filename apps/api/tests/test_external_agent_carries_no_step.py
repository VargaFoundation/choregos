"""An external agent never carries a step of a workflow (S65-20, ADR 0163 §1).

An `external` agent is a client of the MCP door (ADR 0033). Before S65-20 a workflow could name one as a
step's actor: the publication passed, and `resolve_agent` handed the run its version as if it were an agent
the platform runs (no runtime: an ACP run). It is refused where a workflow naming it is published and when
a run resolves it; a `remote` agent (ADR 0062) and an internal one are unchanged. A project may still pin
one: a pin names no step, and an agent's retirement reads its pins.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .agent_scopes_kit import ADMIN, as_user, create_agent, make_project


async def _publish(client: AsyncClient, project_id: str, agent: str) -> tuple[int, str]:
    """The default workflow, its implementing actor naming `agent`."""
    from choregos_core.dsl import template_yaml

    yaml = template_yaml("default-simple").replace(
        'dev: { type: agent, role: implement, model: "profile:by_size" }',
        f'dev: {{ type: agent, role: implement, model: "profile:by_size", agent: {agent} }}',
    )
    assert f"agent: {agent}" in yaml
    response = await client.put(
        f"/api/v1/projects/{project_id}/workflows/default-simple", json={"yaml": yaml}
    )
    return response.status_code, response.text


async def _resolve(project_id: str, slug: str) -> Any:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope
    from choregos_api.services.agents import resolve_agent

    async with session_scope() as session:
        row = await session.get(Project, project_id)
        assert row is not None
        return await resolve_agent(session, row.org_id, row.id, slug)


async def _external(client: AsyncClient, slug: str = "claude-code") -> None:
    await as_user(client, ADMIN)
    created = await create_agent(client, slug, kind="external")
    assert created.status_code == 201, created.text
    assert created.json()["kind"] == "external"


async def test_a_workflow_naming_an_external_agent_is_not_published(client: AsyncClient, org: str) -> None:
    dev = await make_project(client, "dev")
    await _external(client)
    status, text = await _publish(client, dev["id"], "claude-code")
    assert status == 422, text
    assert "claude-code" in text and "external agent" in text and "Name a person" in text
    # An internal agent of the same organisation is still named.
    await create_agent(client, "org-helper")
    status, text = await _publish(client, dev["id"], "org-helper")
    assert status == 200, text


async def test_a_run_does_not_resolve_an_external_agent(client: AsyncClient, org: str) -> None:
    """The execution's guard, for a workflow published before the publication refused it."""
    from choregos_api.services.agents import AgentUnavailable

    dev = await make_project(client, "dev")
    await _external(client)
    with pytest.raises(AgentUnavailable, match="external agent"):
        await _resolve(dev["id"], "claude-code")
    with pytest.raises(AgentUnavailable, match="external agent"):
        await _resolve(dev["id"], "claude-code@1")
    await create_agent(client, "org-helper")
    assert (await _resolve(dev["id"], "org-helper")).agent.kind == "internal"


def test_the_rule_reads_the_kind_before_the_scope() -> None:
    from choregos_api.services.agent_governance import carries_no_step, usable_in_project

    assert carries_no_step("internal", "a") is None
    assert carries_no_step("remote", "a") is None
    assert "external agent" in (carries_no_step("external", "a") or "")
    assert "external agent" in (usable_in_project("org", None, "a", "p1", kind="external") or "")
    assert usable_in_project("org", None, "a", "p1", kind="remote") is None
