"""A project owns the knowledge bases created from it (S39-14, ADR 0051 amendment).

Its owners (`project:write` on it) create, feed, sync and distil them, and may widen or narrow its
reach (ADR 0081, amended); an editor may not. A base nobody can read does not exist for them (404).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import login

ORG = "/api/v1/orgs/varga/knowledge"


async def _member(email: str, role: str, project_slug: str | None = None) -> None:
    from choregos_api.db.models import Membership, Organization, Project, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            user = User(email=email, display_name=email.split("@", maxsplit=1)[0])
            session.add(user)
            await session.flush()
        project_id = None
        if project_slug:
            project_id = (
                await session.execute(
                    select(Project.id).where(Project.org_id == org.id, Project.slug == project_slug)
                )
            ).scalar_one()
        session.add(Membership(user_id=user.id, org_id=org.id, project_id=project_id, role=role))


@asynccontextmanager
async def _as(client: AsyncClient, email: str) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=client._transport.app), base_url="http://test") as c:  # type: ignore[attr-defined]
        await login(c, email)
        yield c


async def _checkout(client: AsyncClient) -> dict[str, Any]:
    payload = {
        "slug": "checkout-web",
        "name": "Checkout web",
        "config": {
            "slug": "checkout-web",
            "org": "varga",
            "repo": {
                "url": "https://github.com/varga/checkout-web.git",
                "default_branch": "main",
                "language": "python",
            },
        },
    }
    created = await client.post("/api/v1/orgs/varga/projects", json=payload)
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _owned_base(owner: AsyncClient, project: dict[str, Any], slug: str = "runbooks") -> dict[str, Any]:
    created = await owner.post(
        f"/api/v1/projects/{project['id']}/knowledge", json={"slug": slug, "name": "Runbooks"}
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


@pytest.fixture
async def owners(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    """billing-api: an owner and a developer; checkout-web: its own owner."""
    other = await _checkout(client)
    await _member("owner@varga.dev", "project_owner", "billing-api")
    await _member("dev@varga.dev", "developer", "billing-api")
    await _member("other@varga.dev", "project_owner", "checkout-web")
    return other


async def test_a_project_owner_creates_a_base_for_their_project(
    client: AsyncClient, project: dict[str, Any], owners: dict[str, Any]
) -> None:
    async with _as(client, "owner@varga.dev") as owner:
        created = await owner.post(
            f"/api/v1/projects/{project['id']}/knowledge", json={"slug": "runbooks", "name": "Runbooks"}
        )
        assert created.status_code == 201, created.text
        body = created.json()
        assert (body["scope"], body["projects"], body["owner_project"]) == (
            "projects",
            ["billing-api"],
            "billing-api",
        )
        duplicate = await owner.post(
            f"/api/v1/projects/{project['id']}/knowledge", json={"slug": "runbooks", "name": "Again"}
        )
        assert duplicate.status_code == 409


async def test_a_developer_cannot(
    client: AsyncClient, project: dict[str, Any], owners: dict[str, Any]
) -> None:
    async with _as(client, "dev@varga.dev") as dev:
        refused = await dev.post(
            f"/api/v1/projects/{project['id']}/knowledge", json={"slug": "x-1", "name": "X"}
        )
        assert refused.status_code == 403, refused.text


async def test_the_owner_curates_and_may_widen(
    client: AsyncClient, project: dict[str, Any], owners: dict[str, Any]
) -> None:
    async with _as(client, "owner@varga.dev") as owner:
        await _owned_base(owner, project)
        sent = await owner.post(f"{ORG}/runbooks/documents", files={"file": ("notes.md", b"# Notes\n")})
        assert sent.status_code == 202, sent.text
        source = await owner.post(
            f"{ORG}/runbooks/sources",
            json={"kind": "web", "name": "docs", "config": {"url": "https://example.org/docs"}},
        )
        assert source.status_code == 201, source.text
        renamed = await owner.patch(f"{ORG}/runbooks", json={"name": "Billing runbooks"})
        assert renamed.status_code == 200 and renamed.json()["owner_project"] == "billing-api"
        distilled = await owner.post(f"{ORG}/runbooks/distill")
        assert distilled.status_code != 403, distilled.text
        assert renamed.json()["can"]["widen"] is True
        for widening in ({"scope": "org"}, {"projects": ["billing-api", "checkout-web"]}, {"groups": ["rh"]}):
            done = await owner.patch(f"{ORG}/runbooks", json=widening)
            assert done.status_code == 200, (widening, done.text)
        restricted = await owner.patch(f"{ORG}/runbooks", json={"restricted": True})
        assert restricted.status_code == 200 and restricted.json()["restricted"] is True
        narrowed = await owner.patch(
            f"{ORG}/runbooks", json={"scope": "projects", "projects": ["billing-api"]}
        )
        assert narrowed.status_code == 200, narrowed.text
        assert narrowed.json()["owner_project"] == "billing-api", "the owner keeps a base it widened"
        again = await owner.post(f"{ORG}/runbooks/documents", files={"file": ("more.md", b"# More\n")})
        assert again.status_code == 202, again.text


async def test_a_developer_cannot_widen(
    client: AsyncClient, project: dict[str, Any], owners: dict[str, Any]
) -> None:
    async with _as(client, "owner@varga.dev") as owner:
        await _owned_base(owner, project)
    async with _as(client, "dev@varga.dev") as dev:
        for widening in (
            {"scope": "org"},
            {"projects": ["billing-api", "checkout-web"]},
            {"restricted": True},
        ):
            refused = await dev.patch(f"{ORG}/runbooks", json=widening)
            assert refused.status_code == 403, (widening, refused.text)


async def test_an_administrator_widening_still_ends_the_projects_ownership(
    client: AsyncClient, project: dict[str, Any], owners: dict[str, Any]
) -> None:
    async with _as(client, "owner@varga.dev") as owner:
        await _owned_base(owner, project)
    widened = await client.patch(f"{ORG}/runbooks", json={"scope": "org"})
    assert widened.status_code == 200, widened.text
    assert widened.json()["owner_project"] is None, "an org base is no project's to curate"
    async with _as(client, "owner@varga.dev") as owner:
        again = await owner.post(f"{ORG}/runbooks/documents", files={"file": ("more.md", b"# More\n")})
        assert again.status_code == 403, "once the administrator widened it, the owner no longer curates it"


async def test_another_projects_owner_cannot_curate(
    client: AsyncClient, project: dict[str, Any], owners: dict[str, Any]
) -> None:
    """The dev login gives every member the organisation's `developer` role, so the base is
    readable here: what is under test is that owning checkout-web does not curate billing-api's."""
    async with _as(client, "owner@varga.dev") as owner:
        await _owned_base(owner, project)
    async with _as(client, "other@varga.dev") as other:
        sent = await other.post(f"{ORG}/runbooks/documents", files={"file": ("x.md", b"# X\n")})
        assert sent.status_code == 403, sent.text
        assert (await other.patch(f"{ORG}/runbooks", json={"name": "x"})).status_code == 403
        assert (await other.post(f"{ORG}/runbooks/distill")).status_code == 403


async def test_a_developer_reads_an_owned_base_and_cannot_curate_it(
    client: AsyncClient, project: dict[str, Any], owners: dict[str, Any]
) -> None:
    async with _as(client, "owner@varga.dev") as owner:
        await _owned_base(owner, project)
    async with _as(client, "dev@varga.dev") as dev:
        assert (await dev.get(f"{ORG}/runbooks")).status_code == 200
        sent = await dev.post(f"{ORG}/runbooks/documents", files={"file": ("x.md", b"# X\n")})
        assert sent.status_code == 403, sent.text


async def test_an_org_admin_keeps_every_right_over_an_owned_base(
    client: AsyncClient, project: dict[str, Any], owners: dict[str, Any]
) -> None:
    async with _as(client, "owner@varga.dev") as owner:
        await _owned_base(owner, project)
    sent = await client.post(f"{ORG}/runbooks/documents", files={"file": ("a.md", b"# A\n")})
    assert sent.status_code == 202, sent.text


async def test_an_owned_base_is_still_served_to_its_project_only(
    client: AsyncClient, project: dict[str, Any], owners: dict[str, Any]
) -> None:
    from choregos_api.knowledge.access import served_to_project

    async with _as(client, "owner@varga.dev") as owner:
        base = await _owned_base(owner, project)
    from types import SimpleNamespace

    served = SimpleNamespace(
        scope=base["scope"],
        projects=base["projects"],
        groups=[],
        status=base["status"],
        owner_project="billing-api",
    )
    assert served_to_project(served, "billing-api")
    assert not served_to_project(served, "checkout-web")


async def test_the_owning_project_and_its_curators(client: AsyncClient, admin: str) -> None:
    """`can_curate` is `can_manage`, or `project:write` on the owning project."""
    from types import SimpleNamespace

    from choregos_api.knowledge.access import can_curate
    from choregos_api.rbac import Principal, Role

    owner = Principal(user_id="u", email="o@x", project_roles={"varga/billing-api": Role.PROJECT_OWNER})
    developer = Principal(user_id="d", email="d@x", project_roles={"varga/billing-api": Role.DEVELOPER})
    mine = SimpleNamespace(owner_project="billing-api")
    unowned = SimpleNamespace(owner_project=None)
    assert can_curate(owner, "varga", mine)
    assert not can_curate(owner, "varga", unowned)
    assert not can_curate(developer, "varga", mine)
    assert not can_curate(owner, "varga", SimpleNamespace(owner_project="checkout-web"))
