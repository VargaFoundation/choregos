# SPDX-License-Identifier: Apache-2.0
"""A person's personal space (S63-03, ADR 0134): one per person and organisation, never listed, read by its
owner only."""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from .conftest import login, without_postgres


async def _user(email: str) -> Any:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        return (await session.execute(select(User).where(User.email == email))).scalar_one()


async def _space_of(org_slug: str, email: str) -> str:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from choregos_api.services.personal_spaces import personal_space

    user = await _user(email)
    async with session_scope(orgs="*") as session:
        organisation = (
            await session.execute(select(Organization).where(Organization.slug == org_slug))
        ).scalar_one()
        return (await personal_space(session, organisation, user)).id


# -- on any database ---------------------------------------------------------------------------------------


async def test_a_person_has_one_space_per_organisation(client: AsyncClient, admin: str) -> None:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope
    from choregos_api.services.personal_spaces import personal_slug

    first = await _space_of("varga", admin)
    assert await _space_of("varga", admin) == first  # created once, found after
    async with session_scope(orgs="*") as session:
        spaces = (await session.execute(select(Project).where(Project.kind == "personal"))).scalars().all()
    assert [(s.id, s.slug, s.owner_user_id) for s in spaces] == [
        (first, personal_slug((await _user(admin)).id), (await _user(admin)).id)
    ]


async def test_a_personal_space_is_not_listed_even_to_its_owner(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    await _space_of("varga", admin)
    listing = await client.get("/api/v1/orgs/varga/projects")
    assert listing.status_code == 200, listing.text
    assert [p["slug"] for p in listing.json()["items"]] == ["billing-api"]


async def test_the_mcp_door_does_not_list_a_personal_space(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    await _space_of("varga", admin)
    token = await client.post("/api/v1/me/tokens", json={"name": "mcp", "scopes": ["mcp:read"]})
    assert token.status_code == 201, token.text
    answer = await client.post(
        "/mcp",
        headers={"Authorization": f"Bearer {token.json()['token']}"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "list_projects"}},
    )
    assert answer.status_code == 200, answer.text
    listed = answer.json()["result"]["structuredContent"]["projects"]
    assert [p["project"] for p in listed] == ["varga:billing-api"]


@pytest.mark.parametrize("slug", ["personal-0123456789ab", "personal-x"])
async def test_a_team_project_may_not_take_a_personal_slug(
    client: AsyncClient, admin: str, slug: str
) -> None:
    created = await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": slug, "name": "X", "config": {"slug": slug, "org": "varga"}},
    )
    assert created.status_code == 422, created.text
    assert "reserved" in created.json()["detail"]


async def test_a_personal_space_has_an_owner_and_a_team_project_none(client: AsyncClient, admin: str) -> None:
    """The constraint holds in the database, not only in the service."""
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        async with session_scope(orgs="*") as session:
            organisation = (
                await session.execute(select(Organization).where(Organization.slug == "varga"))
            ).scalar_one()
            session.add(Project(org_id=organisation.id, slug="orphan", name="O", config={}, kind="personal"))
            await session.flush()


# -- on PostgreSQL: the owner reads it, nobody else does ----------------------------------------------------


@pytest.fixture
async def organisation_a(pg_app: Any) -> str:
    """The organisation `a`, which sign-in joins (`OIDC_DEFAULT_ORG=a`)."""
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        organisation = Organization(slug="a", name="A")
        session.add(organisation)
        await session.flush()
        return organisation.id


@without_postgres
async def test_another_person_never_reads_a_personal_space(pg_app: Any, organisation_a: str) -> None:
    """Even an administrator of the organisation, through the API and straight through RLS; and what the space
    holds (a work item) follows the space."""
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import limit_to_organisations, name_the_user, session_scope

    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "admin@a.test")  # org_admin of `a` (OIDC_DEFAULT_ORG=a, DEV_ADMIN_EMAILS)
        await login(client, "dev@a.test")  # a member of `a`
        space = await _space_of("a", "dev@a.test")
        async with session_scope(orgs="*") as session:
            session.add(WorkItem(project_id=space, tracker_key="p-1", title="A brief", state="ready"))

        dev, admin = await _user("dev@a.test"), await _user("admin@a.test")
        async with session_scope(orgs=["a"]) as session:
            await name_the_user(session, admin.id)
            assert await session.get(Project, space) is None
            assert (await session.execute(select(WorkItem).where(WorkItem.project_id == space))).all() == []
        async with session_scope(orgs=["a"]) as session:
            await name_the_user(session, dev.id)
            assert (await session.get(Project, space)) is not None
            assert (
                len((await session.execute(select(WorkItem).where(WorkItem.project_id == space))).all()) == 1
            )
        async with session_scope() as session:  # no user named: nobody's space
            await limit_to_organisations(session, ["a"])
            assert await session.get(Project, space) is None

        await login(client, "admin@a.test")
        assert (await client.get(f"/api/v1/projects/{space}")).status_code == 404
        listing = (await client.get("/api/v1/orgs/a/projects")).json()
        assert all(not p["slug"].startswith("personal-") for p in listing["items"])


@without_postgres
async def test_the_platform_scope_reads_every_personal_space(pg_app: Any, organisation_a: str) -> None:
    """Tasks run by the orchestrator (ADR 0137) work in the subscriber's space under the platform scope."""
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "dev@a.test")
    space = await _space_of("a", "dev@a.test")
    async with session_scope(orgs="*") as session:
        assert (await session.get(Project, space)) is not None
