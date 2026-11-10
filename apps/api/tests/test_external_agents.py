"""External agents (ADR 0033, S18-06): a client of the MCP door attached to an agent.

Its rights are the human's, intersected with what the agent's version names; its revocation is read
again on every call.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login


async def _external_agent(client: AsyncClient, tools: list[str] | None, slug: str = "claude-of-lea") -> None:
    spec: dict[str, Any] = {
        "mcp_servers": [{"connector": "choregos", "tools": tools}] if tools is not None else []
    }
    created = await client.post(
        "/api/v1/orgs/varga/agents",
        json={"slug": slug, "kind": "external", "display_name": "Lea's Claude Code", "spec": spec},
    )
    assert created.status_code == 201, created.text


async def _token(client: AsyncClient, scope: str = "mcp:write") -> tuple[str, str]:
    created = await client.post("/api/v1/me/tokens", json={"name": "claude-code", "scopes": [scope]})
    assert created.status_code == 201, created.text
    return str(created.json()["id"]), str(created.json()["token"])


async def _attach(client: AsyncClient, token_id: str, slug: str = "claude-of-lea") -> Any:
    return await client.post(
        f"/api/v1/orgs/varga/agents/{slug}/credentials", json={"kind": "token", "token_id": token_id}
    )


async def _tools(client: AsyncClient, token: str) -> Any:
    return await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}"},
    )


async def test_an_external_agent_sees_only_what_its_version_names(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _external_agent(client, ["list_projects", "search_*"])
    token_id, token = await _token(client)
    attached = await _attach(client, token_id)
    assert attached.status_code == 201, attached.text
    client.cookies.clear()
    names = {o["name"] for o in (await _tools(client, token)).json()["result"]["tools"]}
    assert names == {"list_projects", "search_work_items"}
    call = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "create_work_item", "arguments": {}},
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert call.json()["error"]["code"] == -32602, "outside the agent's version: like an unknown tool"


async def test_a_revoked_agent_gets_401_on_the_next_call(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _external_agent(client, None)
    token_id, token = await _token(client)
    await _attach(client, token_id)
    assert (await _tools(client, token)).status_code == 200
    revoked = await client.patch("/api/v1/orgs/varga/agents/claude-of-lea", json={"status": "revoked"})
    assert revoked.status_code == 200
    refused = await _tools(client, token)
    assert refused.status_code == 401 and "revoked" in refused.text


async def test_a_human_without_rights_gives_an_agent_without_rights(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _external_agent(client, ["list_projects", "create_work_item"])
    member = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "reader@varga.dev", "role": "viewer"}
    )
    assert member.status_code in {200, 201}
    await login(client, "reader@varga.dev")
    token_id, token = await _token(client)
    await login(client, "admin@varga.dev")
    assert (await _attach(client, token_id)).status_code == 201
    client.cookies.clear()
    names = {o["name"] for o in (await _tools(client, token)).json()["result"]["tools"]}
    assert names == {"list_projects"}, "the version allows create_work_item, the person cannot"


async def test_the_audit_names_the_agent(client: AsyncClient, project: dict[str, Any]) -> None:
    await _external_agent(client, None)
    token_id, token = await _token(client)
    await _attach(client, token_id)
    call = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_projects"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert call.status_code == 200, call.text
    audit = (await client.get("/api/v1/audit")).json()["items"]
    (row,) = [a for a in audit if a["action"] == "mcp.call"]
    assert row["payload"]["agent"] == "claude-of-lea"


@pytest.mark.parametrize(
    ("body", "expected_status"),
    [
        pytest.param({"kind": "token"}, 422, id="no-token"),
        pytest.param({"kind": "token", "token_id": "unknown"}, 422, id="unknown-token"),
        pytest.param({"kind": "oauth_client"}, 422, id="no-client"),
    ],
)
async def test_what_cannot_be_attached(
    client: AsyncClient, admin: str, body: dict[str, Any], expected_status: int
) -> None:
    await _external_agent(client, None)
    assert (
        await client.post("/api/v1/orgs/varga/agents/claude-of-lea/credentials", json=body)
    ).status_code == expected_status


async def test_a_token_of_someone_outside_the_organisation_cannot_be_attached(
    client: AsyncClient, admin: str
) -> None:
    """Finding #1520: knowing a token's id is not enough to bind it to an agent of another organisation."""
    from choregos_api.db.models import ApiToken, User
    from choregos_api.db.session import session_scope
    from choregos_api.security import generate_api_token

    await _external_agent(client, None)
    async with session_scope(orgs="*") as session:
        stranger = User(email="stranger@elsewhere.example", display_name="stranger")
        session.add(stranger)
        await session.flush()
        _, digest = generate_api_token()
        foreign = ApiToken(user_id=stranger.id, name="theirs", hash=digest, scopes=["mcp:write"])
        session.add(foreign)
        await session.flush()
        foreign_id = foreign.id
    refused = await _attach(client, foreign_id)
    assert refused.status_code == 422, refused.text
    assert (
        refused.json()
        == (await _attach(client, "no-such-token")).json() | {"instance": refused.json().get("instance")}
        or refused.json()["detail"] == (await _attach(client, "no-such-token")).json()["detail"]
    )
    # A member's token still attaches.
    token_id, _ = await _token(client)
    assert (await _attach(client, token_id)).status_code == 201


async def test_an_internal_agent_carries_no_client(client: AsyncClient, admin: str) -> None:
    internal = await client.post(
        "/api/v1/orgs/varga/agents", json={"slug": "internal", "display_name": "Internal", "kind": "internal"}
    )
    assert internal.status_code == 201
    token_id, _ = await _token(client)
    assert (await _attach(client, token_id, slug="internal")).status_code == 422
    await _external_agent(client, None)
    assert (await _attach(client, token_id)).status_code == 201
    assert (await _attach(client, token_id)).status_code == 409, "one client, one agent"


async def test_an_oauth_client_embodies_its_agent(client: AsyncClient, admin: str) -> None:
    """The OAuth path (S15-08): the token's `azp` designates the attached client."""
    from choregos_api.db.session import session_scope
    from choregos_api.mcp.caller import Caller, act_as_agent
    from choregos_api.rbac import Principal

    await _external_agent(client, ["list_projects"])
    attached = await client.post(
        "/api/v1/orgs/varga/agents/claude-of-lea/credentials",
        json={"kind": "oauth_client", "client_id": "claude-ai"},
    )
    assert attached.status_code == 201, attached.text
    caller = Caller(principal=Principal(user_id="u", email="x@y"), token=None, scopes=frozenset({"mcp:read"}))
    async with session_scope(orgs="*") as session:
        await act_as_agent(session, caller, client_id="claude-ai")
    assert (caller.agent, caller.reasons) == ("claude-of-lea", ("list_projects",))


async def test_an_attached_client_says_when_it_called_and_with_what(client: AsyncClient, admin: str) -> None:
    """The Agents page shows a "connected" Claude Code (S18-07): the last call of its token to the door,
    and the client that made it. Before any call, nothing is invented."""
    await _external_agent(client, None)
    token_id, token = await _token(client)
    assert (await _attach(client, token_id)).status_code == 201
    (before,) = (await client.get("/api/v1/orgs/varga/agents/claude-of-lea/credentials")).json()
    assert before["token_name"] == "claude-code"
    assert before["last_used_at"] is None and before["last_client"] is None

    cookies = dict(client.cookies)
    client.cookies.clear()
    call = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {token}", "User-Agent": "claude-code/2.1.0"},
    )
    assert call.status_code == 200, call.text
    client.cookies.update(cookies)
    (after,) = (await client.get("/api/v1/orgs/varga/agents/claude-of-lea/credentials")).json()
    assert after["last_used_at"] is not None
    assert after["last_client"] == "claude-code/2.1.0"


async def test_a_client_gets_the_tools_of_the_production_revision_not_the_latest(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """S40-02: with `on_publish: none`, a saved revision that widens the tool patterns is not in
    production, so the door does not honour it (red with the "latest revision" query)."""
    await _external_agent(client, ["list_projects"])
    widened = {"mcp_servers": [{"connector": "choregos", "tools": ["list_projects", "search_*"]}]}
    policy = await client.put(
        "/api/v1/orgs/varga/agents/claude-of-lea/release-policy", json={"on_publish": "none"}
    )
    assert policy.status_code == 200, policy.text
    saved = await client.post("/api/v1/orgs/varga/agents/claude-of-lea/versions", json=widened)
    assert saved.status_code == 201, saved.text
    token_id, token = await _token(client)
    assert (await _attach(client, token_id)).status_code == 201
    client.cookies.clear()
    names = {o["name"] for o in (await _tools(client, token)).json()["result"]["tools"]}
    assert names == {"list_projects"}
