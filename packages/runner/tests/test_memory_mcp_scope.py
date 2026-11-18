"""The agent's memory MCP goes through the runner's loopback with the credential and the run's tenant
(2026-11-18): the agent holds no key, cannot name another tenant, and calls only the tools of its binding.

Measured in prod (Choregos 0.27.13): the memory server was announced to the agent bare, and Ecphoria
answered every call 401 (`auth_enabled`). Handing it the deployment's key would have been worse: the key
carries no tenant (`<key>@:admin`), and Ecphoria's MCP takes the tenant from the call's own `tenant_id`.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from tranibor_adapters.fakes.mcp import FakeMcpServer
from tranibor_adapters.memory.ecphoria import MCP_READ_TOOLS, MCP_WRITE_TOOLS, EcphoriaMemory
from tranibor_contracts import McpServerRef, StageInput
from tranibor_core.hosted import loopback_url
from tranibor_runner.guardrails import GuardRails
from tranibor_runner.loopback.agent import agent_view, without_unenforceable_servers
from tranibor_runner.loopback.mcp import McpProxy

pytestmark = pytest.mark.asyncio

KEY = "-".join(["mem", "key", "5e1c" * 4])
PORTS = (4101, 4102, 4103)
EVERY_TOOL = [
    {"name": name, "inputSchema": {"type": "object"}}
    for name in (
        *MCP_READ_TOOLS,
        *MCP_WRITE_TOOLS,
        "query",
        "ingest",
        "set_state",
        "delete_memory",
        "update_memory",
    )
]


def _memory(writes: bool) -> McpServerRef:
    adapter = EcphoriaMemory("http://ecphoria:8432", token=KEY)
    return adapter.mcp_server("billing-api", writes=writes)


def _step(stage_input: StageInput, writes: bool = True) -> StageInput:
    return stage_input.model_copy(
        update={"tools": stage_input.tools.model_copy(update={"mcp": {"memory": _memory(writes)}})}
    )


class Agent:
    """What the agent's MCP client does: JSON-RPC on the runner's loopback."""

    def __init__(self, stage_input: StageInput, server: FakeMcpServer) -> None:
        self.recorded: list[tuple[str, dict[str, Any]]] = []

        async def record(type_: str, payload: dict[str, Any]) -> None:
            self.recorded.append((type_, payload))

        self.proxy = McpProxy(
            stage_input,
            record,
            GuardRails(stage_input.permissions, list(stage_input.allowed_paths), workspace="/workspace"),
            transport=server.transport(),
            verbatim=True,
        )
        self.server = server

    async def post(self, body: Any) -> httpx.Response:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.proxy.app()), base_url="http://127.0.0.1:4002"
        ) as client:
            return await client.post(
                "/mcp/memory",
                json=body,
                headers={"accept": "application/json", "mcp-session-id": self.server.session},
            )

    async def call(self, tool: str, **arguments: Any) -> httpx.Response:
        return await self.post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool, "arguments": arguments},
            }
        )


def _server() -> FakeMcpServer:
    return FakeMcpServer(tools=EVERY_TOOL, token=KEY, page_size=100)


# ───────────────────────── what the agent is told ─────────────────────────


def test_the_agent_is_told_the_loopback_and_holds_neither_the_key_nor_the_tenant(
    stage_input: StageInput,
) -> None:
    step = _step(stage_input)

    view = agent_view(step, PORTS)

    assert view.tools.mcp["memory"] == McpServerRef(url=f"{loopback_url(PORTS[1])}/mcp/memory")
    assert KEY not in view.model_dump_json()
    assert step.tools.mcp["memory"].env == {"Authorization": f"Bearer {KEY}"}, "the runner's copy keeps it"


def test_a_scoped_server_with_no_header_is_still_served_on_the_loopback(stage_input: StageInput) -> None:
    """Dev, no auth: no key to hide, but the tenant and the tools are still the runner's."""
    bare = EcphoriaMemory("http://ecphoria:8432").mcp_server("billing-api", writes=False)
    assert bare.env == {}
    step = stage_input.model_copy(
        update={"tools": stage_input.tools.model_copy(update={"mcp": {"memory": bare}})}
    )
    assert agent_view(step, PORTS).tools.mcp["memory"].url == f"{loopback_url(PORTS[1])}/mcp/memory"


def test_without_a_container_the_agent_is_not_handed_the_key(stage_input: StageInput) -> None:
    step = _step(stage_input)

    kept, dropped = without_unenforceable_servers(step)

    assert dropped == ["memory"] and "memory" not in kept.tools.mcp
    assert KEY not in kept.model_dump_json()


def test_the_adapter_scopes_the_server_to_its_tenant_and_the_mode_of_the_binding() -> None:
    reading, writing = _memory(False), _memory(True)

    assert reading.url == "http://ecphoria:8432/mcp"
    assert reading.scope_arguments == {"tenant_id": "billing-api"}
    assert reading.allowed_tools == list(MCP_READ_TOOLS)
    assert writing.allowed_tools == [*MCP_READ_TOOLS, *MCP_WRITE_TOOLS]
    # Tools that take the tenant from the credential (which has none) or a bare id never leave the platform.
    for tool in ("query", "ingest", "set_state", "embed", "memory_scopes", "delete_memory", "update_memory"):
        assert tool not in writing.allowed_tools
    assert EcphoriaMemory("http://e:1", token=KEY, tenant="acme").mcp_server(
        "billing-api", writes=False
    ).scope_arguments == {"tenant_id": "acme"}, "the tenant the orchestrator itself writes under"


# ───────────────────────── what the loopback does for it ─────────────────────────


async def test_the_runner_adds_the_key_and_forces_the_tenant_over_the_agents(stage_input: StageInput) -> None:
    agent = Agent(_step(stage_input), _server())

    answer = await agent.call("search_memory", query="jwks", tenant_id="another-project", k=3)

    assert answer.status_code == 200
    [(tool, arguments)] = agent.server.calls
    assert tool == "search_memory"
    assert arguments == {"query": "jwks", "k": 3, "tenant_id": "billing-api"}, "the agent's tenant is gone"
    [(_, headers, _)] = [r for r in agent.server.received if r[2].get("method") == "tools/call"]
    assert headers["authorization"] == f"Bearer {KEY}"


async def test_a_call_that_names_no_tenant_gets_the_run_project(stage_input: StageInput) -> None:
    agent = Agent(_step(stage_input), _server())

    await agent.call("add_memory", content="the jwks cache is 10 minutes")

    assert agent.server.calls == [
        ("add_memory", {"content": "the jwks cache is 10 minutes", "tenant_id": "billing-api"})
    ]


@pytest.mark.parametrize("tool", ["query", "ingest", "set_state", "delete_memory", "update_memory"])
async def test_a_tool_outside_the_grant_never_reaches_the_server(stage_input: StageInput, tool: str) -> None:
    agent = Agent(_step(stage_input), _server())

    answer = await agent.call(tool, sql="select * from events", id="x")

    assert answer.json()["result"]["isError"] is True
    assert agent.server.calls == []
    assert (
        "hosted.mcp_refused",
        {"server": "memory", "tool": tool, "reason": "tool_not_granted"},
    ) in agent.recorded


@pytest.mark.parametrize("tool", MCP_WRITE_TOOLS)
async def test_a_read_binding_cannot_write(stage_input: StageInput, tool: str) -> None:
    agent = Agent(_step(stage_input, writes=False), _server())

    refused = await agent.call(tool, content="x")
    read = await agent.call("search_memory", query="x")

    assert refused.json()["result"]["isError"] is True
    assert read.status_code == 200
    assert [name for name, _ in agent.server.calls] == ["search_memory"]


async def test_a_batch_cannot_carry_a_call_past_the_checks(stage_input: StageInput) -> None:
    agent = Agent(_step(stage_input), _server())
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "query", "arguments": {}}}

    answer = await agent.post([call])

    assert answer.status_code == 400
    assert agent.server.calls == []


async def test_the_agent_lists_only_the_tools_it_may_call(stage_input: StageInput) -> None:
    agent = Agent(_step(stage_input, writes=False), _server())

    answer = await agent.post({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert [t["name"] for t in answer.json()["result"]["tools"]] == list(MCP_READ_TOOLS)


async def test_a_server_the_runner_does_not_scope_is_relayed_as_before(stage_input: StageInput) -> None:
    open_ref = McpServerRef(url="http://docs.internal/mcp")
    step = stage_input.model_copy(
        update={"tools": stage_input.tools.model_copy(update={"mcp": {"memory": open_ref}})}
    )
    agent = Agent(step, FakeMcpServer(tools=EVERY_TOOL))

    await agent.call("query", sql="select 1", tenant_id="anything")

    assert agent.server.calls == [("query", {"sql": "select 1", "tenant_id": "anything"})]
    assert json.dumps(open_ref.model_dump())  # no scope: the contract's defaults
    assert not open_ref.scoped
