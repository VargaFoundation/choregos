"""What the tool server lists to the model of the API's tool list (S57-05, ADR 0122).

Since S57-04 a relayed call's `structuredContent` is the API's whole answer and its notices, never the
operation's output. An MCP client validates `structuredContent` against a tool's `outputSchema`: the
schema the API announces for the bridge (S57-07) would make it refuse every answer. The platform's
`x-choregos-*` keys are not MCP's either. MCP's own `annotations` reach the model.
"""

from __future__ import annotations

import json
from typing import Any

from choregos_tools_mcp import McpServer
from choregos_tools_mcp.server import listed_to_the_model

OUTPUT = {"type": "object", "required": ["quantity"], "properties": {"quantity": {"type": "integer"}}}
API_TOOLS: list[dict[str, Any]] = [
    {"name": "supplier__stock", "description": "[supplier] the stock", "inputSchema": {"type": "object"},
     "outputSchema": OUTPUT, "annotations": {"readOnlyHint": True, "x-choregos-access": "read"},
     "x-choregos-access": "read"},
    {"name": "supplier__order_pc", "description": "[supplier] orders a PC", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": False}, "x-choregos-access": "approval", "x-choregos-other": 1},
]  # fmt: skip


class ApiTools:
    """The internal API as the tool server sees it: the run's tool list, and the answer to a call."""

    async def fetch_tools(self) -> list[dict[str, Any]]:
        return [dict(tool) for tool in API_TOOLS]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"status_code": 200, "result": {"structuredContent": {"quantity": 3}}, "remaining": None}


async def test_the_model_s_list_never_carries_an_output_schema_nor_the_platform_s_keys() -> None:
    server = McpServer(ApiTools())  # type: ignore[arg-type]
    listed = await server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed is not None
    tools = {tool["name"]: tool for tool in listed["result"]["tools"]}
    text = json.dumps(listed)
    assert "outputSchema" not in text and "x-choregos-" not in text
    assert tools["supplier__stock"] == {
        "name": "supplier__stock",
        "description": "[supplier] the stock",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    }
    assert tools["supplier__order_pc"]["annotations"] == {"readOnlyHint": False}, "MCP's annotations stay"
    assert "report_finding" in tools, "the server's own tools are listed as before"


async def test_a_stripped_tool_is_still_called_through_the_api() -> None:
    server = McpServer(ApiTools())  # type: ignore[arg-type]
    called = await server.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "supplier__stock", "arguments": {}},
        }
    )
    assert called is not None and called["result"]["isError"] is False, called


def test_the_api_s_entry_is_not_changed_in_place() -> None:
    entry = dict(API_TOOLS[0])
    listed_to_the_model(entry)
    assert entry == API_TOOLS[0]
