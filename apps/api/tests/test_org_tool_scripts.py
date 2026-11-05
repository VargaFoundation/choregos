"""Tool scripts are switched on per organisation (S57-05b, ADR 0122 §6 amended 2026-11-05).

`PATCH /orgs/{org}` with `tool_scripts`: `enabled`, `disabled`, or `null` to follow the deployment's
default. Its administrator only — a project owner and a platform administrator who is not one are refused
— audited in `org.update`, and stored under the key the orchestrator reads
(`choregos_core.tool_scripts_switch`). `GET /orgs` shows it to every member, so a project's settings can
say when the organisation has them disabled.
"""

from __future__ import annotations

from typing import Any

from choregos_core.tool_scripts_switch import ORG_SETTING, org_tool_scripts
from httpx import AsyncClient

from .conftest import login
from .kits.organisation import declare_administrator

ORG = "/api/v1/orgs"


async def _stored() -> dict[str, Any]:
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        organisation = (
            await session.execute(select(Organization).where(Organization.slug == "varga"))
        ).scalar_one()
        return dict(organisation.settings or {})


async def _audit_rows(client: AsyncClient) -> list[dict[str, Any]]:
    items = (await client.get("/api/v1/audit")).json()["items"]
    return [a for a in items if a["action"] == "org.update"]


async def _shown(client: AsyncClient) -> Any:
    (organisation,) = (await client.get(ORG)).json()
    return organisation["tool_scripts"]


async def test_an_organisation_follows_the_deployment_s_default_until_it_chooses(
    client: AsyncClient, admin: str
) -> None:
    assert await _shown(client) is None
    assert ORG_SETTING not in await _stored()


async def test_its_administrator_enables_then_disables_then_returns_to_the_default(
    client: AsyncClient, admin: str
) -> None:
    enabled = await client.patch(f"{ORG}/varga", json={"tool_scripts": "enabled"})
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["tool_scripts"] == "enabled"
    assert await _shown(client) == "enabled"
    stored = await _stored()
    assert stored[ORG_SETTING] == "enabled"
    assert org_tool_scripts(stored) == "enabled", "the orchestrator reads what the API wrote"

    renamed = await client.patch(f"{ORG}/varga", json={"name": "Varga"})
    assert renamed.json()["tool_scripts"] == "enabled", "a request without the field leaves it"

    disabled = await client.patch(f"{ORG}/varga", json={"tool_scripts": "disabled"})
    assert disabled.status_code == 200 and disabled.json()["tool_scripts"] == "disabled"
    assert org_tool_scripts(await _stored()) == "disabled"

    default = await client.patch(f"{ORG}/varga", json={"tool_scripts": None})
    assert default.status_code == 200 and default.json()["tool_scripts"] is None
    assert ORG_SETTING not in await _stored(), "`null` removes the choice: the deployment's default again"
    assert await _shown(client) is None

    rows = await _audit_rows(client)
    changes = [(r["payload"]["before"]["tool_scripts"], r["payload"]["after"]["tool_scripts"]) for r in rows]
    assert len(changes) == 4 and set(changes) == {
        (None, "enabled"),
        ("enabled", "enabled"),
        ("enabled", "disabled"),
        ("disabled", None),
    }, "each change is audited with what it was and what it became"
    assert {r["actor_id"] for r in rows} == {(await client.get("/api/v1/me")).json()["id"]}


async def test_a_value_outside_the_contract_is_refused(client: AsyncClient, admin: str) -> None:
    for value in ("on", "offer", True, "ENABLED"):
        refused = await client.patch(f"{ORG}/varga", json={"tool_scripts": value})
        assert refused.status_code == 422, (value, refused.text)
    assert ORG_SETTING not in await _stored()


async def test_a_project_owner_cannot_change_it_and_nothing_is_audited(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    member = {"email": "lead@varga.dev", "role": "project_owner"}
    assert (await client.post(f"{ORG}/varga/members", json=member)).status_code in {200, 201}
    await login(client, "lead@varga.dev")
    refused = await client.patch(f"{ORG}/varga", json={"tool_scripts": "enabled"})
    assert refused.status_code == 403, refused.text
    assert ORG_SETTING not in await _stored()
    await login(client, "admin@varga.dev")
    assert await _audit_rows(client) == []


async def test_a_member_reads_the_organisation_s_choice(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    """The project's Tool scripts card says when the organisation has them disabled: a member reads it."""
    assert (await client.patch(f"{ORG}/varga", json={"tool_scripts": "disabled"})).status_code == 200
    member = {"email": "dev@varga.dev", "role": "developer", "project_slug": "billing-api"}
    assert (await client.post(f"{ORG}/varga/members", json=member)).status_code in {200, 201}
    await login(client, "dev@varga.dev")
    assert await _shown(client) == "disabled"


async def _remove_the_memberships(email: str) -> None:
    from choregos_api.db.models import Membership, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete, select

    async with session_scope(orgs="*") as session:
        me = (await session.execute(select(User).where(User.email == email))).scalar_one()
        await session.execute(delete(Membership).where(Membership.user_id == me.id))


async def test_a_platform_administrator_who_is_not_its_administrator_is_refused(
    client: AsyncClient, admin: str
) -> None:
    """The platform administrator renames an organisation he is not a member of; its tool scripts are its
    administrator's choice, so the whole request is refused and nothing of it is applied."""
    from choregos_api.edition import reset

    await declare_administrator({"platform@example.dev"})
    try:
        await login(client, "platform@example.dev")
        await _remove_the_memberships("platform@example.dev")
        refused = await client.patch(f"{ORG}/varga", json={"name": "Elsewhere", "tool_scripts": "enabled"})
        assert refused.status_code == 403, refused.text
        assert "org_admin" in refused.json()["detail"]
        renamed = await client.patch(f"{ORG}/varga", json={"name": "Elsewhere"})
        assert renamed.status_code == 200, "the name alone is still his to change"
        assert renamed.json()["tool_scripts"] is None
        assert ORG_SETTING not in await _stored()
    finally:
        reset()
