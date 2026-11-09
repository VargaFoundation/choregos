"""Rename an organisation (`PATCH /orgs/{org}`).

The Choregos dev ran in an organisation `diametral`, the name of the host, in an open source
project, and nothing could rename it; the community edition holds only one, so nothing could create
another one to move to either. Its name is changed by its administrator; its slug, by the platform
administrator, because it is in every URL, every MCP address and the name of the IdP groups. The
projects follow: they belong to the organisation by its identifier.

On PostgreSQL (the CI suite), RLS compares the slugs: renaming in a transaction bounded to the old
slug would be refused, which this file also proves.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .conftest import login
from .kits.organisation import declare_administrator

ORG = "/api/v1/orgs"


async def test_the_slug_changes_and_the_projects_follow(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    renamed = await client.patch(f"{ORG}/varga", json={"slug": "trust", "name": "Varga Trust"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json() == {
        "slug": "trust",
        "name": "Varga Trust",
        "role": "org_admin",
        "currency": "EUR",
        "aliases": ["varga"],
        "tool_scripts": None,
        "complete_agents": None,
    }
    # The old slug is an alias now (ADR 0090): the old address still resolves.
    assert (await client.get(f"{ORG}/varga/projects")).status_code == 200
    projects_of_org = (await client.get(f"{ORG}/trust/projects")).json()["items"]
    assert [p["slug"] for p in projects_of_org] == ["billing-api"]
    read = await client.get("/api/v1/projects/trust:billing-api")
    assert read.status_code == 200, "the qualified identifier follows the new slug"
    assert {o["slug"] for o in (await client.get(ORG)).json()} == {"trust"}
    (row,) = [a for a in (await client.get("/api/v1/audit")).json()["items"] if a["action"] == "org.update"]
    assert row["payload"]["before"] == {
        "slug": "varga",
        "name": "Varga Foundation",
        "currency": "EUR",
        "tool_scripts": None,
        "complete_agents": None,
    }
    assert row["payload"]["after"] == {
        "slug": "trust",
        "name": "Varga Trust",
        "currency": "EUR",
        "tool_scripts": None,
        "complete_agents": None,
    }


async def test_the_name_alone_changes_without_touching_the_slug(client: AsyncClient, admin: str) -> None:
    renamed = await client.patch(f"{ORG}/varga", json={"name": "Varga"})
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["slug"] == "varga"
    assert {o["name"] for o in (await client.get(ORG)).json()} == {"Varga"}


async def _another_organisation(also_admin: bool) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        other = Organization(slug="other", name="Other")
        session.add(other)
        await session.flush()
        if also_admin:
            me = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
            session.add(Membership(user_id=me.id, org_id=other.id, role="org_admin"))


async def test_the_slug_is_an_instance_gesture(client: AsyncClient, admin: str) -> None:
    """Administering ONE organisation out of two does not administer the instance: the slug is refused,
    the name is changed."""
    await _another_organisation(also_admin=False)
    refusal = await client.patch(f"{ORG}/varga", json={"slug": "free"})
    assert refusal.status_code == 403, refusal.text
    assert (await client.patch(f"{ORG}/varga", json={"name": "Varga"})).status_code == 200
    assert {o["slug"] for o in (await client.get(ORG)).json()} == {"varga"}


async def test_a_taken_slug_is_a_conflict(client: AsyncClient, admin: str) -> None:
    await _another_organisation(also_admin=True)
    refusal = await client.patch(f"{ORG}/varga", json={"slug": "other"})
    assert refusal.status_code == 409, refusal.text
    assert "varga" in {o["slug"] for o in (await client.get(ORG)).json()}


async def test_a_project_owner_does_not_rename_the_organisation(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    member = {"email": "lead@varga.dev", "role": "project_owner"}
    assert (await client.post(f"{ORG}/varga/members", json=member)).status_code in {200, 201}
    await login(client, "lead@varga.dev")
    refusal = await client.patch(f"{ORG}/varga", json={"name": "My home"})
    assert refusal.status_code == 403, refusal.text
    await login(client, "admin@varga.dev")
    assert {o["name"] for o in (await client.get(ORG)).json()} == {"Varga Foundation"}


async def test_a_malformed_slug_is_refused(client: AsyncClient, admin: str) -> None:
    assert (await client.patch(f"{ORG}/varga", json={"slug": "Not Good"})).status_code == 422


# ── S42-01 : an instance administrator granted by an edition plugin, member of nothing ──


async def _remove_the_memberships(email: str) -> None:
    """The dev login enrols a newcomer in the default organisation: undo it, he is a real outsider."""
    from choregos_api.db.models import Membership, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete, select

    async with session_scope(orgs="*") as session:
        me = (await session.execute(select(User).where(User.email == email))).scalar_one()
        await session.execute(delete(Membership).where(Membership.user_id == me.id))


async def test_a_platform_administrator_who_is_not_a_member_renames_name_and_slug(
    client: AsyncClient, admin: str
) -> None:
    from choregos_api.edition import reset

    await declare_administrator({"platform@example.dev"})
    try:
        await login(client, "platform@example.dev")
        await _remove_the_memberships("platform@example.dev")
        assert (await client.get(ORG)).json() == [], "he belongs to no organisation"
        renamed = await client.patch(f"{ORG}/varga", json={"name": "Diametral", "slug": "diametral"})
        assert renamed.status_code == 200, renamed.text
        assert renamed.json() == {
            "slug": "diametral",
            "name": "Diametral",
            "role": None,
            "currency": "EUR",
            "aliases": ["varga"],
            "tool_scripts": None,
            "complete_agents": None,
        }
        await login(client, "admin@varga.dev")
        assert {(o["slug"], o["name"]) for o in (await client.get(ORG)).json()} == {
            ("diametral", "Diametral")
        }
        (row,) = [
            a for a in (await client.get("/api/v1/audit")).json()["items"] if a["action"] == "org.update"
        ]
        assert row["payload"]["before"]["slug"] == "varga"
        assert row["payload"]["after"] == {
            "slug": "diametral",
            "name": "Diametral",
            "currency": "EUR",
            "tool_scripts": None,
            "complete_agents": None,
        }
    finally:
        reset()


async def test_a_non_member_without_platform_right_is_refused(client: AsyncClient, admin: str) -> None:
    from choregos_api.edition import reset

    await declare_administrator({"platform@example.dev"})
    try:
        await login(client, "curious@example.dev")
        await _remove_the_memberships("curious@example.dev")
        refusal = await client.patch(f"{ORG}/varga", json={"name": "My place"})
        assert refusal.status_code == 403, refusal.text
        await login(client, "admin@varga.dev")
        assert {o["name"] for o in (await client.get(ORG)).json()} == {"Varga Foundation"}
    finally:
        reset()
