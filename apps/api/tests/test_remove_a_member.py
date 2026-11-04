"""Remove a member (S17-02): the administration could only invite or change a role.

The last administrator of an organisation is not removed: it could then be managed only through
the database.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .conftest import login


async def _member(client: AsyncClient, email: str, role: str, project: str | None = None) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/orgs/varga/members", json={"email": email, "role": role, "project_slug": project}
    )
    assert response.status_code in {200, 201}, response.text
    return dict(response.json())


async def _members_of(client: AsyncClient) -> set[tuple[str | None, str | None, str]]:
    rows = (await client.get("/api/v1/orgs/varga/members")).json()
    return {(m["email"], m.get("project_slug"), m["role"]) for m in rows}


async def test_a_member_is_removed_and_the_audit_says_so(client: AsyncClient, admin: str) -> None:
    dev = await _member(client, "dev@varga.dev", "developer")
    removed = await client.delete(f"/api/v1/orgs/varga/members/{dev['user_id']}")
    assert removed.status_code == 204, removed.text
    assert ("dev@varga.dev", None, "developer") not in await _members_of(client)
    audit = (await client.get("/api/v1/audit")).json()["items"]
    assert any(a["action"] == "member.remove" and a["target_id"] == dev["user_id"] for a in audit)


async def test_the_last_administrator_is_not_removed(client: AsyncClient, admin: str) -> None:
    me = next(m for m in (await client.get("/api/v1/orgs/varga/members")).json() if m["email"] == admin)
    alone = await client.delete(f"/api/v1/orgs/varga/members/{me['user_id']}")
    assert alone.status_code == 409 and "last administrator" in alone.text
    await _member(client, "second@varga.dev", "org_admin")
    assert (await client.delete(f"/api/v1/orgs/varga/members/{me['user_id']}")).status_code == 204


async def test_removing_from_a_project_leaves_the_organisation(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    dev = await _member(client, "dev@varga.dev", "developer")
    await _member(client, "dev@varga.dev", "project_owner", project="billing-api")
    removed = await client.delete(
        f"/api/v1/orgs/varga/members/{dev['user_id']}", params={"project": "billing-api"}
    )
    assert removed.status_code == 204, removed.text
    remaining = await _members_of(client)
    assert ("dev@varga.dev", None, "developer") in remaining
    assert ("dev@varga.dev", "billing-api", "project_owner") not in remaining


async def test_a_developer_removes_nobody(client: AsyncClient, admin: str) -> None:
    other = await _member(client, "other@varga.dev", "viewer")
    await _member(client, "dev@varga.dev", "developer")
    await login(client, "dev@varga.dev")
    assert (await client.delete(f"/api/v1/orgs/varga/members/{other['user_id']}")).status_code == 403
