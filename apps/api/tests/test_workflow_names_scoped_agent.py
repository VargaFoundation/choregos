"""A workflow may not name a personal agent, nor another project's agent (S39-09, ADR 0061).

Publication is refused with a 422 that names the agent, its scope and the fix; an agent of the
project itself, an organisation agent and a common one are fine.
"""

from __future__ import annotations

from httpx import AsyncClient

from .agent_scopes_kit import ADMIN, as_user, create_agent, install_common, make_project


async def _publish(client: AsyncClient, project_id: str, agent: str) -> tuple[int, str]:
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


async def test_a_workflow_names_only_agents_that_may_run_in_its_project(
    client: AsyncClient, org: str
) -> None:
    dev = await make_project(client, "dev")
    await make_project(client, "ops")
    await as_user(client, ADMIN)
    await create_agent(client, "my-helper", scope="personal")
    await create_agent(client, "ops-helper", scope="project", project="ops")
    await create_agent(client, "dev-helper", scope="project", project="dev")
    await create_agent(client, "org-helper")
    await install_common(client)

    status, text = await _publish(client, dev["id"], "my-helper")
    assert status == 422 and "my-helper" in text and "personal" in text and "Promote it" in text
    status, text = await _publish(client, dev["id"], "ops-helper")
    assert status == 422 and "ops-helper" in text and "another project" in text and "Fork it" in text
    for allowed in ("dev-helper", "org-helper", "developer"):
        status, text = await _publish(client, dev["id"], allowed)
        assert status == 200, text
