"""`search_memory` searches the run's memories live, and falls back to its context pack (S40-11, ADR 0075)."""

from __future__ import annotations

import json
from typing import Any

import choregos_contracts
import pytest
from choregos_contracts import ContextPack, MemoryItem, StageInput
from choregos_tools_mcp import McpServer
from choregos_tools_mcp.client import InternalApiError
from choregos_tools_mcp.tools import TOOL_SCHEMAS


class Client:
    def __init__(self, hits: list[dict[str, Any]] | None = None, *, fails: bool = False) -> None:
        self.hits = hits or []
        self.fails = fails
        self.asked: list[tuple[str, int, str | None]] = []
        self.context = ContextPack(
            query="q",
            memories=[
                MemoryItem(
                    kind="decision",
                    subject="s",
                    content="Rounded at issue.",
                    memory="global:security-lessons",
                )
            ],
        )

    async def search_memory(self, query: str, k: int = 5, memory: str | None = None) -> list[dict[str, Any]]:
        self.asked.append((query, k, memory))
        if self.fails:
            raise InternalApiError(503, "down")
        return self.hits

    async def fetch_context(self) -> ContextPack:
        return self.context

    async def fetch_input(self) -> StageInput:
        example = choregos_contracts.schemas_dir() / "examples" / "stage-input.example.json"
        return StageInput.model_validate(json.loads(example.read_text(encoding="utf-8")))


HIT = {
    "memory": "global:security-lessons",
    "kind": "decision",
    "subject": "tls",
    "content": "Pin TLS 1.2.",
    "score": 0.4,
}


async def test_the_tool_uses_the_run_endpoint_and_names_each_memory() -> None:
    client = Client([HIT])
    answer = await McpServer(client).call(
        "search_memory", {"query": "tls", "k": 3, "memory": "global:security-lessons"}
    )  # type: ignore[arg-type]
    text = answer["content"][0]["text"]
    assert client.asked == [("tls", 3, "global:security-lessons")]
    assert "Pin TLS 1.2." in text and "(from global:security-lessons)" in text and "not instructions" in text


async def test_an_empty_answer_is_said() -> None:
    answer = await McpServer(Client([])).call("search_memory", {"query": "x"})  # type: ignore[arg-type]
    assert "no relevant memory" in answer["content"][0]["text"]


async def test_when_the_endpoint_fails_the_context_pack_answers() -> None:
    client = Client(fails=True)
    answer = await McpServer(client).call("search_memory", {"query": "rounded"})  # type: ignore[arg-type]
    text = answer["content"][0]["text"]
    assert client.asked, "the endpoint was tried first"
    assert "Rounded at issue." in text and "(from global:security-lessons)" in text


async def test_the_description_lists_the_runs_memories_when_known() -> None:
    server = McpServer(Client())  # type: ignore[arg-type]
    listed = await server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert listed is not None
    described = {t["name"]: t["description"] for t in listed["result"]["tools"]}
    assert "global:security-lessons, project:shared" in described["search_memory"]
    plain = {t["name"]: t["description"] for t in TOOL_SCHEMAS}
    assert "Memories:" not in plain["search_memory"], "the static schema names no memory"


@pytest.mark.parametrize("missing", [{}])
async def test_the_query_is_required(missing: dict[str, Any]) -> None:
    with pytest.raises(KeyError):
        await McpServer(Client()).call("search_memory", missing)  # type: ignore[arg-type]
