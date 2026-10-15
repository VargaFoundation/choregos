"""The bridges build a framework's tools whose calls go through `Tools.call` (S39-17e).

The frameworks are stubbed in `sys.modules`: none is a dependency of the SDK or of the lock file.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from choregos_agent_sdk import PermissionDenied
from choregos_agent_sdk.integrations import langchain, openai_agents, pydantic_ai
from choregos_agent_sdk.tools import ToolResult, Tools, ToolSpec

from .fakes import FakeConn, make_session

SPEC = ToolSpec(
    "choregos-tools",
    "knowledge_search",
    "Search.",
    {"type": "object", "properties": {"query": {"type": "string"}}},
)


class _Client:
    def __init__(self) -> None:
        self.called: list[tuple[str, dict[str, Any]]] = []

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.called.append((name, arguments))
        block = types.SimpleNamespace(text="hit")
        return types.SimpleNamespace(content=[block], is_error=False, structured_content=None)


def _tools(tmp_path: Path, allow: bool) -> tuple[Tools, FakeConn, _Client]:
    conn = FakeConn(allow=allow)
    tools = Tools(make_session(tmp_path, conn))
    client = _Client()
    tools._connected = True  # no MCP server to open: the specs and the client are given
    tools._clients = {"choregos-tools": client}
    tools._specs = {SPEC.name: SPEC}
    return tools, conn, client


def _stub(monkeypatch: pytest.MonkeyPatch, name: str, **attrs: Any) -> None:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    monkeypatch.setitem(sys.modules, name, module)


class _Record:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


async def test_the_langchain_bridge_goes_through_the_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "langchain_core")
    _stub(monkeypatch, "langchain_core.tools", StructuredTool=_Record)
    tools, conn, client = _tools(tmp_path, allow=True)
    (tool,) = await langchain.tools(tools)
    assert (tool.name, tool.args_schema) == ("knowledge_search", SPEC.input_schema)
    assert await tool.coroutine(query="refunds") == "hit"
    assert conn.asked == ["choregos-tools/knowledge_search"] and client.called == [
        ("knowledge_search", {"query": "refunds"})
    ]
    denied, _, denied_client = _tools(tmp_path, allow=False)
    (blocked,) = await langchain.tools(denied)
    with pytest.raises(PermissionDenied):
        await blocked.coroutine(query="refunds")
    assert denied_client.called == [], "a refusal means no call"


async def test_the_openai_agents_bridge_goes_through_the_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub(monkeypatch, "agents", FunctionTool=_Record)
    tools, conn, client = _tools(tmp_path, allow=True)
    (tool,) = await openai_agents.tools(tools)
    assert tool.params_json_schema == SPEC.input_schema and tool.strict_json_schema is False
    assert await tool.on_invoke_tool(None, json.dumps({"query": "x"})) == "hit"
    assert conn.asked and client.called == [("knowledge_search", {"query": "x"})]
    denied, _, denied_client = _tools(tmp_path, allow=False)
    (blocked,) = await openai_agents.tools(denied)
    with pytest.raises(PermissionDenied):
        await blocked.on_invoke_tool(None, "{}")
    assert denied_client.called == []


async def test_the_pydantic_ai_bridge_goes_through_the_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Tool:
        @staticmethod
        def from_schema(function: Any, name: str, description: str, json_schema: dict[str, Any]) -> _Record:
            return _Record(function=function, name=name, json_schema=json_schema)

    _stub(monkeypatch, "pydantic_ai", Tool=Tool)
    _stub(monkeypatch, "pydantic_ai.toolsets", FunctionToolset=lambda tools: _Record(tools=tools))
    tools, conn, client = _tools(tmp_path, allow=True)
    toolset = await pydantic_ai.toolset(tools)
    assert await toolset.tools[0].function(query="x") == "hit"
    assert conn.asked and client.called == [("knowledge_search", {"query": "x"})]
    denied, _, denied_client = _tools(tmp_path, allow=False)
    blocked = (await pydantic_ai.toolset(denied)).tools[0]
    with pytest.raises(PermissionDenied):
        await blocked.function(query="x")
    assert denied_client.called == []


async def test_a_missing_framework_says_what_to_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "langchain_core.tools", None)  # makes the import fail
    tools, _, _ = _tools(tmp_path, allow=True)
    with pytest.raises(ImportError, match="install langchain-core"):
        await langchain.tools(tools)


def test_the_tool_result_is_what_the_bridges_read() -> None:
    assert ToolResult(text="a").text == "a"
