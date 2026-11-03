"""An organisation administrator links a member to their GitHub numeric user id (S47-14, ADR 0102)."""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient
from sqlalchemy import select

from .conftest import login


async def _member(client: AsyncClient, email: str, role: str) -> str:
    added = await client.post("/api/v1/orgs/varga/members", json={"email": email, "role": role})
    assert added.status_code == 201, added.text
    return str(added.json()["user_id"])


def _url(user_id: str, tracker: str = "github") -> str:
    return f"/api/v1/orgs/varga/members/{user_id}/tracker-identities/{tracker}"


async def test_an_admin_links_a_github_identity_and_a_developer_cannot(
    client: AsyncClient, admin: str
) -> None:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    dev = await _member(client, "dev@varga.dev", "developer")
    other = await _member(client, "other@varga.dev", "developer")

    linked = await client.put(_url(dev), json={"external_id": "1001", "login": "devon"})
    assert linked.status_code == 200, linked.text
    assert linked.json()["external_id"] == "1001" and linked.json()["user_id"] == dev
    assert linked.json()["tracker"] == "github" and linked.json()["created_by"] == admin

    again = await client.put(_url(dev), json={"external_id": "1002", "login": "devon"})
    assert again.status_code == 200 and again.json()["external_id"] == "1002", "a member has one link"
    listed = await client.get(f"/api/v1/orgs/varga/members/{dev}/tracker-identities")
    assert [row["external_id"] for row in listed.json()] == ["1002"]

    taken = await client.put(_url(other), json={"external_id": "1002", "login": "devon"})
    assert taken.status_code == 409, taken.text
    for bad in ("devon", "12 3", ""):
        refused = await client.put(_url(dev), json={"external_id": bad, "login": "devon"})
        assert refused.status_code == 422, bad

    await login(client, "dev@varga.dev")
    assert (await client.put(_url(other), json={"external_id": "7", "login": "x"})).status_code == 403
    assert (await client.get(f"/api/v1/orgs/varga/members/{dev}/tracker-identities")).status_code == 403
    assert (await client.delete(_url(dev))).status_code == 403

    await login(client, admin)
    assert (await client.delete(_url(dev))).status_code == 204
    assert (await client.delete(_url(dev))).status_code == 404
    unknown = await client.put(_url("nobody"), json={"external_id": "5", "login": "x"})
    assert unknown.status_code == 404

    async with session_scope() as session:
        actions = (await session.execute(select(AuditLog.action))).scalars().all()
    assert actions.count("tracker_identity.set") == 2
    assert actions.count("tracker_identity.delete") == 1


async def test_a_project_member_is_not_an_organisation_member(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    added = await client.post(
        "/api/v1/orgs/varga/members",
        json={"email": "scoped@varga.dev", "role": "developer", "project_slug": "billing-api"},
    )
    assert added.status_code == 201, added.text
    linked = await client.put(_url(added.json()["user_id"]), json={"external_id": "9", "login": "scoped"})
    assert linked.status_code == 404, "the link is a member of the organisation's"
