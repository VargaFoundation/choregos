"""An organisation's slug is an alias; its id is its identity (ADR 0090).

Each test renames `varga` to `fondation` and proves that an address, a group or a session that
carried the OLD slug still reaches the organisation. Every one fails without the alias table.
"""

from __future__ import annotations

from typing import Any

from httpx import ASGITransport, AsyncClient

ORG = "/api/v1/orgs"


async def _rename(client: AsyncClient, slug: str = "fondation") -> None:
    done = await client.patch(f"{ORG}/varga", json={"slug": slug})
    assert done.status_code == 200, done.text


async def test_an_old_url_still_reaches_the_organisation_and_names_the_canonical_one(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    await _rename(client)
    old = await client.get(f"{ORG}/varga/projects")
    assert old.status_code == 200, old.text
    assert [p["slug"] for p in old.json()["items"]] == ["billing-api"]
    assert old.headers["link"] == f'<{ORG}/fondation/projects>; rel="canonical"'
    assert "link" not in (await client.get(f"{ORG}/fondation/projects")).headers
    # a write through the old address lands on the same organisation
    renamed = await client.patch(f"{ORG}/varga", json={"name": "Fondation"})
    assert renamed.status_code == 200 and renamed.json()["slug"] == "fondation"


async def test_an_old_qualified_id_and_mcp_address_still_resolve_to_the_project(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.deps import resolve_project

    await _rename(client)
    for address in ("varga:billing-api", "varga/billing-api", "fondation:billing-api"):
        async with session_scope(orgs="*") as session:
            found, org_slug = await resolve_project(session, address)
        assert (found.id, org_slug) == (project["id"], "fondation"), address
    assert (await client.get("/api/v1/projects/varga:billing-api")).status_code == 200


async def test_an_old_idp_group_still_maps_and_the_current_one_too(client: AsyncClient, admin: str) -> None:
    from choregos_api.config import Settings
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from choregos_api.routers.auth import _map_groups_to_roles
    from sqlalchemy import select

    await _rename(client)
    async with session_scope(orgs="*") as session:
        user = User(email="nouvelle@idp.test", display_name="n")
        session.add(user)
        await session.flush()
        await _map_groups_to_roles(session, user, ["choregos:varga:developers"], Settings())
        await session.flush()
        org = (await session.execute(select(Organization))).scalar_one()
        rows = (
            (await session.execute(select(Membership).where(Membership.user_id == user.id))).scalars().all()
        )
        assert [(m.org_id, m.role) for m in rows] == [(org.id, "developer")]
        # the old and the new group name of one organisation: the higher role wins, one membership
        await _map_groups_to_roles(
            session, user, ["choregos:varga:developers", "choregos:fondation:org-admins"], Settings()
        )
        await session.flush()
        rows = (
            (await session.execute(select(Membership).where(Membership.user_id == user.id))).scalars().all()
        )
        assert [(m.org_id, m.role) for m in rows] == [(org.id, "org_admin")]


async def test_a_session_and_a_token_issued_before_the_rename_keep_working(
    client: AsyncClient, admin: str, app: Any
) -> None:
    token = (await client.post("/api/v1/me/tokens", json={"name": "ci", "scopes": ["*"]})).json()["token"]
    await _rename(client)
    mine = await client.get(ORG)  # the cookie of the session opened BEFORE the rename
    assert [(o["slug"], o["role"]) for o in mine.json()] == [("fondation", "org_admin")]
    by_token = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        got = await by_token.get(ORG, headers={"Authorization": f"Bearer {token}"})
        assert got.status_code == 200, got.text
        assert [o["slug"] for o in got.json()] == ["fondation"]
        old = await by_token.get(f"{ORG}/varga/projects", headers={"Authorization": f"Bearer {token}"})
        assert old.status_code == 200, "the token's old address resolves too"
    finally:
        await by_token.aclose()


async def test_a_restart_with_the_old_bootstrap_slug_does_not_create_a_second_organisation(
    client: AsyncClient, admin: str
) -> None:
    from choregos_api.amorcage import amorcer
    from choregos_api.config import Settings

    await _rename(client)
    done = await amorcer(Settings(bootstrap_org="varga", bootstrap_admins="x@varga.dev"))
    assert done == {"organisations": [], "administrateurs": []}
    assert [o["slug"] for o in (await client.get(ORG)).json()] == ["fondation"]


async def _other_organisation(slug: str, *, admin_too: bool = True) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        other = Organization(slug=slug, name=slug)
        session.add(other)
        await session.flush()
        me = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
        if admin_too:
            session.add(Membership(user_id=me.id, org_id=other.id, role="org_admin"))


async def test_no_other_organisation_can_take_an_alias_but_its_owner_can_take_it_back(
    client: AsyncClient, admin: str
) -> None:
    await _other_organisation("autre")
    await _rename(client)  # `varga` is now an alias of `fondation`
    taken = await client.patch(f"{ORG}/autre", json={"slug": "varga"})
    assert taken.status_code == 409, taken.text
    created = await client.post(ORG, json={"slug": "varga", "name": "Squat"})
    assert created.status_code in {403, 409}, created.text
    back = await client.patch(f"{ORG}/fondation", json={"slug": "varga"})
    assert back.status_code == 200, back.text
    assert back.json()["aliases"] == ["fondation"], "the slug it takes back stops being an alias"


async def test_listing_shows_the_aliases_and_releasing_one_frees_it(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    await _rename(client)
    (org,) = (await client.get(ORG)).json()
    assert org["aliases"] == ["varga"]
    # releasing an alias is a platform-administrator act: on an instance with two organisations, the
    # admin of one of them is not one
    await _other_organisation("autre", admin_too=False)
    assert (await client.delete(f"{ORG}/fondation/aliases/varga")).status_code == 403


async def test_releasing_an_alias_makes_the_old_address_a_404(
    app: Any, client: AsyncClient, admin: str
) -> None:
    from choregos_api.edition import declarer_un_administrateur_de_plateforme, reinitialiser

    await _rename(client)
    reinitialiser()
    declarer_un_administrateur_de_plateforme(lambda session, principal: principal.email == "admin@varga.dev")
    try:
        gone = await client.delete(f"{ORG}/fondation/aliases/varga")
        assert gone.status_code == 204, gone.text
        assert (await client.get(f"{ORG}/varga/projects")).status_code == 404
        assert (await client.delete(f"{ORG}/fondation/aliases/varga")).status_code == 404
        assert (await client.get(ORG)).json()[0]["aliases"] == []
        assert (await client.patch(f"{ORG}/fondation", json={"slug": "varga"})).status_code == 200
    finally:
        reinitialiser()


async def test_the_project_config_follows_the_rename(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    assert project["config"]["org"] == "varga"
    await _rename(client)
    read = await client.get("/api/v1/projects/fondation:billing-api")
    assert read.json()["config"]["org"] == "fondation"


async def test_a_background_scope_written_with_the_old_slug_resolves_to_the_organisation(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    """A workflow payload that waited for months names the old slug (SQLite: no RLS, so this proves the
    lookups; the RLS side is in `test_rls_postgres.py`)."""
    from choregos_api.db.models import Project
    from choregos_api.db.org_aliases import organisation_by_slug_or_alias
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _rename(client)
    async with session_scope(orgs=["varga"]) as session:
        assert (await session.execute(select(Project.slug))).scalars().all() == ["billing-api"]
        found = await organisation_by_slug_or_alias(session, "varga")
        assert found is not None and found.slug == "fondation"
