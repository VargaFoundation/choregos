"""Members per scope (S30-06, plan item 9).

Reading an organisation's members is for its members — nothing was checked before (P0). A
project's members are read with `?project=`, by that project's members. `org_admin` is a role of
the organisation: a project row cannot carry it, since a project role only adds to the org role.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .conftest import login

MEMBRES = "/api/v1/orgs/varga/members"


async def test_a_stranger_does_not_read_the_organisation_s_members(client: AsyncClient, admin: str) -> None:
    assert (await client.get(MEMBRES)).status_code == 200
    client.cookies.clear()
    await login(client, "stranger@elsewhere.dev")
    refuse = await client.get(MEMBRES)
    assert refuse.status_code == 403, refuse.text


async def test_a_project_member_reads_the_project_s_members_and_not_the_organisation_s(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    for corps in (
        {"email": "po@varga.dev", "role": "project_owner", "project_slug": "billing-api"},
        {"email": "dev@varga.dev", "role": "developer", "project_slug": "billing-api"},
        {"email": "orga@varga.dev", "role": "viewer"},
    ):
        assert (await client.post(MEMBRES, json=corps)).status_code == 201
    client.cookies.clear()
    await login(client, "po@varga.dev")
    du_projet = await client.get(MEMBRES, params={"project": "billing-api"})
    assert du_projet.status_code == 200, du_projet.text
    assert {(m["email"], m["role"]) for m in du_projet.json()} == {
        ("po@varga.dev", "project_owner"),
        ("dev@varga.dev", "developer"),
    }
    assert all(m["project_slug"] == "billing-api" for m in du_projet.json())
    # A project role gives no right to the organisation's list.
    assert (await client.get(MEMBRES)).status_code == 403
    # The project owner manages the project's members.
    ajoute = await client.post(
        MEMBRES, json={"email": "rc@varga.dev", "role": "release_captain", "project_slug": "billing-api"}
    )
    assert ajoute.status_code == 201, ajoute.text


async def test_org_admin_is_not_a_project_role(client: AsyncClient, project: dict[str, Any]) -> None:
    refuse = await client.post(
        MEMBRES, json={"email": "x@varga.dev", "role": "org_admin", "project_slug": "billing-api"}
    )
    assert refuse.status_code == 422, refuse.text
    assert (await client.post(MEMBRES, json={"email": "x@varga.dev", "role": "org_admin"})).status_code == 201
