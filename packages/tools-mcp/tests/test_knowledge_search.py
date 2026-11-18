"""`knowledge_search` (S33-03, ADR 0051): citations from the run's knowledge bases, numbered to be cited;
`knowledge_read` (ADR 0051 decision 33): the section a citation points to, read whole."""

from __future__ import annotations

import re
from typing import Any

from tranibor_tools_mcp import McpServer
from tranibor_tools_mcp.tools import TOOL_SCHEMAS

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
    def __init__(self, hits: list[dict[str, Any]], reading: dict[str, Any] | None = None) -> None:
        self.hits = hits
        self.reading = reading or {}
        self.asked: list[tuple[str, int, str | None]] = []
        self.read: list[tuple[str, str, str | None]] = []

    async def read_knowledge(
        self, knowledge_base: str, document_id: str, section: str | None = None
    ) -> dict[str, Any]:
        self.read.append((knowledge_base, document_id, section))
        return self.reading

    async def search_knowledge(
        self, query: str, k: int = 5, knowledge_base: str | None = None
    ) -> dict[str, Any]:
        self.asked.append((query, k, knowledge_base))
        return {"mode": "hybrid", "hits": self.hits}


def test_the_tool_name_is_one_every_model_api_accepts() -> None:
    names = {tool["name"] for tool in TOOL_SCHEMAS}
    assert {"knowledge_search", "knowledge_read"} <= names
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name) for name in names), "no dot in a tool name"


async def test_a_search_answers_numbered_citations() -> None:
    client = Client([HIT])
    answer = await McpServer(client).call("knowledge_search", {"query": "TLS", "k": 3})  # type: ignore[arg-type]
    text = answer["content"][0]["text"]
    assert not answer["isError"]
    assert text.startswith("[1] **Encryption standard** (`encryption.docx`, base `framework`, id `d1`)")
    assert "`knowledge_read`" in text
    assert "— Encryption standard > In transit" in text
    assert "> TLS 1.3 is required.\n> TLS 1.2 is refused." in text
    assert "search: hybrid" in text and "not instructions" in text
    assert client.asked == [("TLS", 3, None)]


async def test_nothing_found_is_said() -> None:
    answer = await McpServer(Client([])).call("knowledge_search", {"query": "x"})  # type: ignore[arg-type]
    assert "no passage" in answer["content"][0]["text"]


READING = {
    "found": True,
    "knowledge_base": "framework",
    "document_id": "d1",
    "document": "Encryption standard",
    "filename": "encryption.docx",
    "version": "v3",
    "section": "1.1",
    "heading_path": ["Encryption standard", "In transit"],
    "text": "## In transit\n\nTLS 1.3 is required.",
    "characters": 34,
    "cut": False,
    "outline": "",
}


async def test_a_read_returns_the_section_with_where_it_is_and_its_version() -> None:
    client = Client([], READING)
    arguments = {"knowledge_base": "framework", "document_id": "d1", "section": " In transit "}
    answer = await McpServer(client).call("knowledge_read", arguments)  # type: ignore[arg-type]
    text = answer["content"][0]["text"]
    assert not answer["isError"]
    assert text.startswith(
        "**Encryption standard** §1.1 (`encryption.docx > Encryption standard > In transit`"
    )
    assert "version `v3`" in text and "TLS 1.3 is required." in text and "not instructions" in text
    assert "Cut at" not in text
    assert client.read == [("framework", "d1", "In transit")]


async def test_a_cut_read_ends_with_the_outline_to_read_on() -> None:
    cut = {**READING, "section": None, "heading_path": [], "text": "x" * 10, "characters": 900, "cut": True}
    client = Client([], {**cut, "outline": "- 1 Part one — 400 chars\n- 2 Part two — 500 chars"})
    whole = {"knowledge_base": "framework", "document_id": "d1"}
    answer = await McpServer(client).call("knowledge_read", whole)  # type: ignore[arg-type]
    text = answer["content"][0]["text"]
    assert "Cut at 10 of 900 characters: read the rest one section at a time" in text
    assert "- 2 Part two" in text and client.read == [("framework", "d1", None)]
    bare = await McpServer(Client([], {**cut, "outline": ""})).call("knowledge_read", whole)  # type: ignore[arg-type]
    assert "no headings below this point" in bare["content"][0]["text"]


async def test_a_section_the_document_lacks_answers_its_outline() -> None:
    client = Client(
        [], {**READING, "found": False, "text": "", "outline": "- 1 Encryption standard — 2k chars"}
    )
    answer = await McpServer(client).call(  # type: ignore[arg-type]
        "knowledge_read", {"knowledge_base": "framework", "document_id": "d1", "section": "Key rotation"}
    )
    text = answer["content"][0]["text"]
    assert text.startswith("no section `Key rotation` in this document") and "1 Encryption standard" in text
