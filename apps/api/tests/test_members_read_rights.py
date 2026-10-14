"""`GET /orgs/{org}/members` requires member read rights (S26-04).

Before, any signed-in user could list the members (e-mails and roles) of any organisation.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from choregos_contracts import Role
from httpx import ASGITransport, AsyncClient

from .conftest import login


@pytest.fixture
def land_in_acme(project: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Once the varga project exists: the dev login makes a new user a developer of EVERY organisation
    unless a default one is set; here it lands in `acme`, so a varga role is only the one a test grants.
    """
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_OIDC_DEFAULT_ORG", "acme")
    reset_settings_cache()
    yield
    reset_settings_cache()


async def _member(email: str, org_slug: str, role: Role, project_slug: str | None = None) -> None:
    from choregos_api.db.models import Membership, Organization, Project, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == org_slug))
        ).scalar_one_or_none()
        if org is None:
            org = Organization(slug=org_slug, name=org_slug)
            session.add(org)
            await session.flush()
        user = User(email=email, display_name=email.split("@", maxsplit=1)[0])
        session.add(user)
        await session.flush()
        project_id = None
        if project_slug:
            found = (
                await session.execute(
                    select(Project).where(Project.slug == project_slug, Project.org_id == org.id)
                )
            ).scalar_one()
            project_id = found.id
        session.add(Membership(user_id=user.id, org_id=org.id, project_id=project_id, role=str(role)))


async def _members_as(client: AsyncClient, email: str, org: str = "varga") -> Any:
    app = client._transport.app  # type: ignore[attr-defined]
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as other:
        await login(other, email)
        return await other.get(f"/api/v1/orgs/{org}/members")


@pytest.mark.usefixtures("land_in_acme")
async def test_a_member_of_another_organisation_gets_403(client: AsyncClient) -> None:
    await _member("stranger@acme.dev", "acme", Role.VIEWER)
    response = await _members_as(client, "stranger@acme.dev")
    assert response.status_code == 403, response.text
    assert "admin@varga.dev" not in response.text
    unknown = await _members_as(client, "stranger@acme.dev", "nowhere")
    assert unknown.status_code == 403, "an organisation's existence is not told to a stranger"


@pytest.mark.usefixtures("land_in_acme")
async def test_a_viewer_of_the_organisation_reads_every_member(client: AsyncClient) -> None:
    await _member("viewer@varga.dev", "varga", Role.VIEWER)
    response = await _members_as(client, "viewer@varga.dev")
    assert response.status_code == 200, response.text
    assert {"admin@varga.dev", "viewer@varga.dev"} <= {m["email"] for m in response.json()}


@pytest.mark.usefixtures("land_in_acme")
async def test_a_project_member_reads_only_the_members_of_its_project(client: AsyncClient) -> None:
    await _member("dev@varga.dev", "varga", Role.DEVELOPER, "billing-api")
    response = await _members_as(client, "dev@varga.dev")
    assert response.status_code == 200, response.text
    rows = response.json()
    assert "dev@varga.dev" in {m["email"] for m in rows}
    # the project's creator is its owner too; the organisation-wide `org_admin` row is not shown
    assert {m["project_slug"] for m in rows} == {"billing-api"}, "not the organisation-wide memberships"
    assert "org_admin" not in {m["role"] for m in rows}
