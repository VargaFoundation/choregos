# SPDX-License-Identifier: Apache-2.0
"""The ontology served at the MCP gate of external clients, with the person's rights (ADR 0030).

A person's MCP client — Claude Code, Cursor… — sees the project's ontology on the project's gate
(`/mcp/projects/{org}:{slug}`). An action can be proposed there if one of the PERSON's roles is in
`permissions.propose`, not because the platform lets its agent; the proposal carries their name, and it
waits for a decision taken in the console — never through the gate.
"""

from __future__ import annotations

import pathlib
from typing import Any

from httpx import AsyncClient

from .helpers import package_files, sign_in

GATE = "/mcp/projects/varga:infra"
PARAMS = {"key": "disk-full", "source": "healthcheck", "check": "disk-usage", "severity": "low"}


async def _token(http: AsyncClient, scope: str = "mcp:write") -> str:
    created = await http.post("/api/v1/me/tokens", json={"name": "claude-code", "scopes": [scope]})
    assert created.status_code == 201, created.text
    return str(created.json()["token"])


async def _rpc(http: AsyncClient, token: str, method: str, params: dict[str, Any] | None = None) -> Any:
    response = await http.post(
        GATE,
        json={"jsonrpc": "2.0", "id": 1, "method": method, **({"params": params} if params else {})},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _ontology(http: AsyncClient, project: dict[str, Any], root: pathlib.Path) -> None:
    response = await http.put(
        f"/api/v1/projects/{project['id']}/ontology", json={"files": package_files(root)}
    )
    assert response.status_code == 200, response.text


async def _member(http: AsyncClient, email: str, role: str) -> None:
    response = await http.post("/api/v1/orgs/varga/members", json={"email": email, "role": role})
    assert response.status_code in {200, 201}, response.text


async def test_an_action_reserved_to_owners_is_not_announced_to_a_developer(
    client: AsyncClient, project: dict[str, Any], core_ref: pathlib.Path, mutate: Any
) -> None:
    before, after = "propose: [role:contributor, system]", "propose: [role:owner, system]"
    mutate("actions/open_finding.yaml", before, after)
    await _ontology(client, project, core_ref)
    await _member(client, "dev@varga.dev", "developer")
    await sign_in(client, "dev@varga.dev")
    token = await _token(client)
    client.cookies.clear()

    names = {o["name"] for o in (await _rpc(client, token, "tools/list"))["result"]["tools"]}
    assert "finding_search" in names, "the ontology's reads pass the gate"
    assert "action_open_finding" not in names
    call = await _rpc(client, token, "tools/call", {"name": "action_open_finding", "arguments": {}})
    assert call["error"]["code"] == -32602, "outside the rights: like an unknown tool"


async def test_a_read_token_sees_no_action(
    client: AsyncClient, project: dict[str, Any], core_ref: pathlib.Path
) -> None:
    await _ontology(client, project, core_ref)
    token = await _token(client, "mcp:read")
    client.cookies.clear()
    names = {o["name"] for o in (await _rpc(client, token, "tools/list"))["result"]["tools"]}
    assert "finding_search" in names
    assert not {n for n in names if n.startswith("action_") and n not in {"action_status", "action_list"}}


async def test_the_proposal_carries_the_persons_name_and_waits_in_the_console(
    client: AsyncClient, project: dict[str, Any], core_ref: pathlib.Path, mutate: Any
) -> None:
    # Nothing is approved automatically any more: the proposal waits for an owner.
    mutate("policies/core_ref.yaml", "when: \"action.risk == 'low'\"", 'when: "false"')
    await _ontology(client, project, core_ref)
    token = await _token(client)
    client.cookies.clear()

    call = await _rpc(
        client,
        token,
        "tools/call",
        {"name": "action_open_finding", "arguments": {"params": PARAMS, "justification": "disk full"}},
    )
    result = call["result"]
    assert result["isError"] is False, result
    proposal = result["structuredContent"]["proposal"]

    pending = (await _rpc(client, token, "tools/call", {"name": "list_pending_decisions"}))["result"]
    (waiting,) = [a for a in pending["structuredContent"]["pending"] if a["key"] == proposal]
    assert waiting["kind"] == "action proposal"
    assert waiting["decision_url"].endswith(f"/p/infra/actions/{proposal}"), "the page of the core's actions"
    assert waiting["can_decide"] is False, "who proposes does not decide (separation of duties)"

    await sign_in(client, "admin@varga.dev")
    read = await client.get(f"/api/v1/projects/{project['id']}/proposals/{proposal}")
    assert read.status_code == 200, read.text
    assert read.json()["proposed_by"] == {"kind": "user", "id": "admin@varga.dev", "via": "mcp"}
    assert read.json()["status"] == "pending_approval"
