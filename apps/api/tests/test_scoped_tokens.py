"""API tokens have a scope, and an MCP token only serves the MCP door (ADR 0030).

A token slipped into the configuration of an MCP client (Claude Code, Cursor, VS Code...) lives there
in clear text. Stolen there, it must open only what it was minted for: neither the REST API, nor the
issuing of another token, nor a decision. And bound to a project, it sees that project only.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .conftest import login


async def _mint(client: AsyncClient, **body: Any) -> Any:
    return await client.post("/api/v1/me/tokens", json={"name": "test", **body})


async def test_by_default_a_token_opens_the_rest_api(client: AsyncClient, admin: str) -> None:
    """The tokens from before the scopes, and the CLI's, keep everything: `*`."""
    created = (await _mint(client)).json()
    assert created["scopes"] == ["*"]
    client.cookies.clear()
    response = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {created['token']}"})
    assert response.status_code == 200, response.text


async def test_an_mcp_token_is_refused_by_the_rest_api(client: AsyncClient, admin: str) -> None:
    created = (await _mint(client, scopes=["mcp:read"])).json()
    assert created["scopes"] == ["mcp:read"]
    client.cookies.clear()
    header = {"Authorization": f"Bearer {created['token']}"}
    for method, path, body in (
        ("GET", "/api/v1/me", None),
        ("GET", "/api/v1/orgs/varga/projects", None),
        ("POST", "/api/v1/me/tokens", {"name": "another one"}),
    ):
        response = await client.request(method, path, headers=header, json=body)
        assert response.status_code == 403, f"{method} {path}: {response.status_code} {response.text}"
        assert "MCP door" in response.json()["detail"]


async def test_the_whole_api_scope_does_not_combine(client: AsyncClient, admin: str) -> None:
    response = await _mint(client, scopes=["*", "mcp:read"])
    assert response.status_code == 422, response.text


async def test_a_project_only_bounds_an_mcp_token(client: AsyncClient, project: dict[str, Any]) -> None:
    response = await _mint(client, scopes=["*"], project="varga:billing-api")
    assert response.status_code == 422, response.text


async def test_an_mcp_token_bound_to_a_project(client: AsyncClient, project: dict[str, Any]) -> None:
    created = await _mint(client, scopes=["mcp:write"], project="varga:billing-api")
    assert created.status_code == 201, created.text
    assert created.json()["project"] == "varga:billing-api"
    listed = (await client.get("/api/v1/me/tokens")).json()
    assert [(t["scopes"], t["project"]) for t in listed] == [(["mcp:write"], "varga:billing-api")]


async def test_an_unknown_or_unreadable_project_is_not_bound(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    response = await _mint(client, scopes=["mcp:read"], project="varga:does-not-exist")
    assert response.status_code == 404, response.text


async def test_the_last_client_is_noted(client: AsyncClient, admin: str) -> None:
    created = (await _mint(client)).json()
    token = created["token"]
    client.cookies.clear()
    headers = {"Authorization": f"Bearer {token}", "User-Agent": "claude-code/2.1"}
    await client.get("/api/v1/me", headers=headers)
    # Read back through a session: reading with the token would note the reader's client.
    await login(client, admin)
    listed = (await client.get("/api/v1/me/tokens")).json()
    assert listed[0]["last_client"] == "claude-code/2.1"
    assert listed[0]["last_used_at"] is not None
