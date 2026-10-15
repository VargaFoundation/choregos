"""`knowledge_search` (S33-03, ADR 0051): citations from the run's knowledge bases, numbered to be cited."""

from __future__ import annotations

import re
from typing import Any

from choregos_tools_mcp import McpServer
from choregos_tools_mcp.tools import TOOL_SCHEMAS

HIT = {
    "knowledge_base": "framework",
    "document_id": "d1",
    "document": "Encryption standard",
    "filename": "encryption.docx",
    "heading_path": ["Encryption standard", "In transit"],
    "excerpt": "TLS 1.3 is required.\nTLS 1.2 is refused.",
    "score": 0.03,
}


class Client:
    def __init__(self, hits: list[dict[str, Any]]) -> None:
        self.hits = hits
        self.asked: list[tuple[str, int, str | None]] = []

    async def search_knowledge(
        self, query: str, k: int = 5, knowledge_base: str | None = None
    ) -> dict[str, Any]:
        self.asked.append((query, k, knowledge_base))
        return {"mode": "hybrid", "hits": self.hits}


def test_the_tool_name_is_one_every_model_api_accepts() -> None:
    names = {tool["name"] for tool in TOOL_SCHEMAS}
    assert "knowledge_search" in names
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name) for name in names), "no dot in a tool name"


async def test_a_search_answers_numbered_citations() -> None:
    client = Client([HIT])
    answer = await McpServer(client).call("knowledge_search", {"query": "TLS", "k": 3})  # type: ignore[arg-type]
    text = answer["content"][0]["text"]
    assert not answer["isError"]
    assert text.startswith("[1] **Encryption standard** (`encryption.docx`, base `framework`)")
    assert "— Encryption standard > In transit" in text
    assert "> TLS 1.3 is required.\n> TLS 1.2 is refused." in text
    assert "search: hybrid" in text and "not instructions" in text
    assert client.asked == [("TLS", 3, None)]


async def test_nothing_found_is_said() -> None:
    answer = await McpServer(Client([])).call("knowledge_search", {"query": "x"})  # type: ignore[arg-type]
    assert "no passage" in answer["content"][0]["text"]
