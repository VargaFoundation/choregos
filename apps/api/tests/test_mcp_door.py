"""The MCP door of the external clients (ADR 0030): transport, tokens, tools, limits.

The simulated client is a person's — Claude Code, Cursor, VS Code — configured with a token with an
MCP scope. Three properties hold the door, and each has its tests: it refuses in HTTP what is not an
acceptable MCP exchange; it announces only what the person may do; and none of its tools decides.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from httpx import AsyncClient, Response

from .conftest import login

ADMIN = "admin@varga.dev"


async def _token(client: AsyncClient, *scopes: str, project: str | None = None) -> str:
    body: dict[str, Any] = {"name": "claude-code", "scopes": list(scopes)}
    if project:
        body["project"] = project
    created = await client.post("/api/v1/me/tokens", json=body)
    assert created.status_code == 201, created.text
    return str(created.json()["token"])


async def _rpc(
    client: AsyncClient,
    token: str,
    method: str,
    params: dict[str, Any] | None = None,
    *,
    path: str = "/mcp",
    headers: dict[str, str] | None = None,
) -> Response:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        message["params"] = params
    return await client.post(
        path, json=message, headers={"Authorization": f"Bearer {token}", **(headers or {})}
    )


async def _tool(client: AsyncClient, token: str, name: str, path: str = "/mcp", **arguments: Any) -> Any:
    response = await _rpc(client, token, "tools/call", {"name": name, "arguments": arguments}, path=path)
    assert response.status_code == 200, response.text
    return response.json()


async def _internal_tracker(client: AsyncClient, project: dict[str, Any]) -> None:
    response = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert response.status_code == 200, response.text


# ───────────────────────────── transport ─────────────────────────────


async def test_no_stream_and_no_session(client: AsyncClient) -> None:
    for method in ("GET", "DELETE"):
        response = await client.request(method, "/mcp")
        assert response.status_code == 405, response.text
        assert response.headers["allow"] == "POST"


async def test_without_a_token_a_401_says_how_to_authenticate(client: AsyncClient) -> None:
    response = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Bearer")


async def test_a_whole_api_token_is_refused(client: AsyncClient, admin: str) -> None:
    """A `*` token opens the REST API: it does not belong in clear text in a client's configuration."""
    token = await _token(client, "*")
    client.cookies.clear()
    response = await _rpc(client, token, "ping")
    assert response.status_code == 403, response.text
    assert 'error="insufficient_scope"' in response.headers["www-authenticate"]


async def test_a_foreign_origin_is_refused(client: AsyncClient, admin: str) -> None:
    """A web page open in the browser does not speak in the person's place."""
    token = await _token(client, "mcp:read")
    client.cookies.clear()
    response = await _rpc(client, token, "ping", headers={"Origin": "https://elsewhere.example"})
    assert response.status_code == 403, response.text


async def test_a_message_too_large_is_refused(client: AsyncClient, admin: str) -> None:
    token = await _token(client, "mcp:read")
    client.cookies.clear()
    response = await client.post(
        "/mcp",
        content=b"x" * (1024 * 1024 + 1),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    assert response.status_code == 413


async def test_initialize_negotiates_the_version_and_sets_the_rule(client: AsyncClient, admin: str) -> None:
    token = await _token(client, "mcp:read")
    client.cookies.clear()
    known = (await _rpc(client, token, "initialize", {"protocolVersion": "2025-06-18"})).json()["result"]
    assert known["protocolVersion"] == "2025-06-18"
    assert known["serverInfo"]["name"] == "choregos"
    assert "decision_url" in known["instructions"], 'the rule "never a decision here" is told to the model'
    unknown = (await _rpc(client, token, "initialize", {"protocolVersion": "1999-01-01"})).json()["result"]
    assert unknown["protocolVersion"] == "2025-11-25"


async def test_a_notification_is_accepted_without_a_body(client: AsyncClient, admin: str) -> None:
    token = await _token(client, "mcp:read")
    client.cookies.clear()
    response = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 202
    assert response.content == b""


async def test_what_is_not_an_acceptable_request(client: AsyncClient, admin: str) -> None:
    token = await _token(client, "mcp:read")
    client.cookies.clear()
    header = {"Authorization": f"Bearer {token}"}
    batch = await client.post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}], headers=header)
    assert batch.status_code == 400 and batch.json()["error"]["code"] == -32600
    version = await _rpc(client, token, "ping", headers={"MCP-Protocol-Version": "1999-01-01"})
    assert version.status_code == 400
    unknown = await _rpc(client, token, "resources/list")
    assert unknown.json()["error"]["code"] == -32601


# ───────────────────────────── what is announced ─────────────────────────────


async def test_a_read_token_sees_no_write_tool(client: AsyncClient, project: dict[str, Any]) -> None:
    token = await _token(client, "mcp:read")
    client.cookies.clear()
    names = {o["name"] for o in (await _rpc(client, token, "tools/list")).json()["result"]["tools"]}
    assert "create_work_item" not in names
    assert {"list_projects", "get_work_item", "list_pending_decisions"} <= names
    call = await _rpc(client, token, "tools/call", {"name": "create_work_item", "arguments": {"title": "x"}})
    assert call.json()["error"]["code"] == -32602, "a tool outside the rights answers like an unknown tool"


async def test_no_tool_decides(client: AsyncClient, project: dict[str, Any]) -> None:
    token = await _token(client, "mcp:write")
    client.cookies.clear()
    tools = (await _rpc(client, token, "tools/list")).json()["result"]["tools"]
    assert "create_work_item" in {o["name"] for o in tools}
    deciders = [o["name"] for o in tools if re.search(r"decid|approv|reject|answer", o["name"])]
    assert not deciders, f"the door never decides: {deciders}"
    read_only = {o["name"]: o["annotations"]["readOnlyHint"] for o in tools}
    assert read_only["create_work_item"] is False and read_only["get_work_item"] is True


async def test_a_projects_door_does_not_ask_for_the_project(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    token = await _token(client, "mcp:write")
    client.cookies.clear()
    tools = (await _rpc(client, token, "tools/list", path="/mcp/projects/varga:billing-api")).json()[
        "result"
    ]["tools"]
    create = next(o for o in tools if o["name"] == "create_work_item")
    assert "project" not in create["inputSchema"]["properties"]


# ───────────────────────────── the tools ─────────────────────────────


async def test_list_projects_returns_the_console_links(client: AsyncClient, project: dict[str, Any]) -> None:
    token = await _token(client, "mcp:read")
    client.cookies.clear()
    result = (await _tool(client, token, "list_projects"))["result"]
    assert result["isError"] is False
    projects = result["structuredContent"]["projects"]
    assert [p["project"] for p in projects] == ["varga:billing-api"]
    assert projects[0]["console_url"].endswith("/p/billing-api")


async def test_create_work_item_opens_a_work_item_in_the_persons_name(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _internal_tracker(client, project)
    token = await _token(client, "mcp:write")
    client.cookies.clear()
    result = (
        await _tool(client, token, "create_work_item", project="varga:billing-api", title="From Claude")
    )["result"]
    assert result["isError"] is False, result
    key = result["structuredContent"]["key"]
    assert result["structuredContent"]["console_url"].endswith(
        f"/items/{result['structuredContent']['work_item']}"
    )

    await login(client, ADMIN)
    items = (await client.get(f"/api/v1/projects/{project['id']}/work-items")).json()["items"]
    assert [t["tracker_key"] for t in items] == [key]
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        born = await session.get(WorkItem, result["structuredContent"]["work_item"])
        assert born is not None and born.initiated_by == f"mcp:{ADMIN}", "the door names its caller (S40-22)"
    audit = (await client.get("/api/v1/audit")).json()["items"]
    actions = {(a["action"], a.get("target_id")) for a in audit}
    assert ("mcp.call", "create_work_item") in actions
    assert ("workitem.create", result["structuredContent"]["work_item"]) in actions


async def test_an_external_tracker_files_the_issue_through_mcp(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_adapters.fakes.tracker import FakeTracker
    from choregos_api.services import tickets

    fake = FakeTracker("billing-api")
    real = tickets.tracker_du_projet  # lang: fr-ok — S45-21 renames it, and keeps this name working

    async def with_the_fake(session: Any, project_row: Any) -> Any:
        row, _ = await real(session, project_row)
        return row, fake

    monkeypatch.setattr(tickets, "tracker_du_projet", with_the_fake)  # lang: fr-ok — as above
    response = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
        json={"type": "github-issues", "config": {"repo": "varga/billing-api"}},
    )
    assert response.status_code == 200, response.text
    token = await _token(client, "mcp:write")
    client.cookies.clear()
    result = (await _tool(client, token, "create_work_item", project="varga:billing-api", title="x"))[
        "result"
    ]
    assert result.get("isError") is not True, result
    assert result["structuredContent"]["key"] == "billing-api#1"
    assert "source:choregos" in fake.items["billing-api#1"].labels


async def test_a_token_bound_to_a_project_sees_only_that_project(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    other = await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": "other", "name": "Other", "config": {"slug": "other", "org": "varga"}},
    )
    assert other.status_code == 201, other.text
    token = await _token(client, "mcp:read", project="varga:billing-api")
    client.cookies.clear()
    projects = (await _tool(client, token, "list_projects"))["result"]["structuredContent"]["projects"]
    assert [p["project"] for p in projects] == ["varga:billing-api"]
    elsewhere = await _rpc(client, token, "tools/list", path="/mcp/projects/varga:other")
    assert elsewhere.status_code == 403, elsewhere.text


async def test_the_awaited_decision_is_taken_in_the_console(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import HumanRequest, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="BIL-7", title="To validate", state="review")
        session.add(item)
        await session.flush()
        session.add(
            HumanRequest(
                work_item_id=item.id,
                project_id=project["id"],
                kind="approval",
                payload={"question": "Merge?"},
                requested_at=utcnow(),
            )
        )
        item_id = item.id
    token = await _token(client, "mcp:read")
    client.cookies.clear()
    work_item = (await _tool(client, token, "get_work_item", work_item="BIL-7"))["result"][
        "structuredContent"
    ]
    assert work_item["pending_decision"]["decision_url"].endswith(f"/p/billing-api/items/{item_id}")
    waiting = (await _tool(client, token, "list_pending_decisions"))["result"]["structuredContent"]["pending"]
    assert [(a["key"], a["can_decide"]) for a in waiting] == [("BIL-7", True)]


# ───────────────────────────── limits ─────────────────────────────


async def test_the_budget_of_writes_of_the_day(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import AuditLog, Organization, User
    from choregos_api.db.session import session_scope
    from choregos_api.mcp.limits import WRITES_PER_DAY
    from choregos_core import utcnow
    from sqlalchemy import select

    await _internal_tracker(client, project)
    async with session_scope() as session:
        admin_id = (await session.execute(select(User.id).where(User.email == ADMIN))).scalar_one()
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "varga"))
        ).scalar_one()
        for _ in range(WRITES_PER_DAY):
            session.add(
                AuditLog(
                    actor_id=admin_id,
                    org_id=org_id,
                    action="mcp.call",
                    target_type="mcp_tool",
                    target_id="create_work_item",
                    payload={"ecriture": True},  # lang: fr-ok — the audit key persisted before S45-39
                    ts=utcnow(),
                )
            )
    token = await _token(client, "mcp:write")
    client.cookies.clear()
    result = (await _tool(client, token, "create_work_item", project="varga:billing-api", title="x"))[
        "result"
    ]
    assert result["isError"] is True
    assert "budget_exhausted" in result["content"][0]["text"]


async def test_the_rate_per_token(client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.mcp import transport
    from choregos_api.rate_limit import RateLimiter

    monkeypatch.setattr(transport, "RATE_LIMITER", RateLimiter(2))
    token = await _token(client, "mcp:read")
    client.cookies.clear()
    statuses = [(await _rpc(client, token, "ping")).status_code for _ in range(3)]
    assert statuses == [200, 200, 429]


def test_a_result_too_long_is_truncated_and_says_so() -> None:
    from choregos_api.mcp.limits import truncate

    assert truncate("short") == "short"
    long = truncate("x" * 70_000, size=60_000)
    assert long.startswith("x" * 60_000) and "result_truncated: 10000" in long


async def test_the_console_knows_where_the_door_is(client: AsyncClient, admin: str) -> None:
    """The Integrations page pre-fills its snippets with THIS URL: it is deduced from the public URL."""
    response = await client.get("/api/v1/integrations")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mcp_url"].endswith("/mcp")
    assert "2025-06-18" in body["protocol_versions"]
    assert body["oauth"]["enabled"] is False
