"""A project's groups are only changed by the organisation's administrator.

ADR 0014 holds access to the catalogue's tools with TWO locks: the deployment says who is
entitled (`groups` on the tool), the project says what it uses (`config.tools`). But the
project's groups lived in the same configuration as its tools, which the project owner can
modify: he opened the tools reserved for another trade by adding himself to its group, and the
second lock held only out of politeness.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .conftest import login


async def _owner(email: str) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        user = User(email=email, display_name="owner")
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org.id, role="project_owner"))


def _config(project: dict[str, Any], **changes: Any) -> dict[str, Any]:
    config = {k: v for k, v in project["config"].items() if v is not None}
    config.update(changes)
    return {"config": config}


async def test_a_project_owner_does_not_open_the_group_of_another_trade(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _owner("owner@varga.dev")
    await login(client, "owner@varga.dev")
    response = await client.patch(f"/api/v1/projects/{project['id']}", json=_config(project, groups=["hr"]))
    assert response.status_code == 403, response.text


async def test_he_keeps_control_of_his_tools(client: AsyncClient, project: dict[str, Any]) -> None:
    """The lock only hinders what it must: changing `tools`, with equal groups, stays with the team."""
    await _owner("owner@varga.dev")
    await login(client, "owner@varga.dev")
    response = await client.patch(f"/api/v1/projects/{project['id']}", json=_config(project, tools=["*"]))
    assert response.status_code == 200, response.text


async def test_the_organisation_administrator_changes_them(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    response = await client.patch(f"/api/v1/projects/{project['id']}", json=_config(project, groups=["hr"]))
    assert response.status_code == 200, response.text
    assert response.json()["config"]["groups"] == ["hr"]
