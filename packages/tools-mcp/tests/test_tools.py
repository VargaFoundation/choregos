"""The MCP sidecar: protocol, tools, caps and the messages sent back to the agent."""

from __future__ import annotations

import json
from typing import Any

import pytest
from choregos_contracts import ContextPack, Finding, MemoryItem
from choregos_tools_mcp import McpServer, ToolContext
from choregos_tools_mcp.tools import TOOL_SCHEMAS


class StubClient:
    def __init__(self) -> None:
        self.findings: list[Finding] = []
        self.questions: list[dict[str, Any]] = []
        self.scope: list[dict[str, Any]] = []
        self.scope_decision: dict[str, Any] = {"decision": "granted", "allowed_paths": ["src/**"]}
        self.ticket = {
            "key": "acme#1",
            "title": "T",
            "body": "B",
            "spec_markdown": "## Spec",
            "plan_markdown": None,
        }
        self.context = ContextPack(
            query="credit notes",
            memories=[
                MemoryItem(kind="decision", subject="decision:billing:rounding", content="Rounded at issue.")
            ],
        )
        self.logs = "ERROR: test_total failed"

    async def report_finding(self, finding: Finding) -> dict[str, Any]:
        self.findings.append(finding)
        return {"accepted": True, "finding_id": f"f{len(self.findings)}", "remaining": 4 - len(self.findings)}

    async def request_scope_change(self, paths: list[str], justification: str) -> dict[str, Any]:
        self.scope.append({"paths": paths, "justification": justification})
        return self.scope_decision

    async def ask_human(self, text: str, options: list[str] | None = None) -> dict[str, Any]:
        self.questions.append({"text": text, "options": options or []})
        return {"status": "accepted"}

    async def fetch_ticket(self) -> dict[str, Any]:
        return self.ticket

    async def fetch_context(self) -> ContextPack:
        return self.context

    async def fetch_ci_logs(self, tail: int = 500) -> str:
        return self.logs


@pytest.fixture
def server() -> tuple[McpServer, StubClient]:
    client = StubClient()
    return McpServer(client, ToolContext(max_findings=2)), client  # type: ignore[arg-type]


async def test_initialize_and_tools_list(server: tuple[McpServer, StubClient]) -> None:
    mcp, _ = server
    init = await mcp.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    assert init is not None and init["result"]["serverInfo"]["name"] == "choregos-tools"
    listed = await mcp.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed is not None
    names = {tool["name"] for tool in listed["result"]["tools"]}
    assert names == {tool["name"] for tool in TOOL_SCHEMAS}
    assert "report_finding" in names and "ask_human" in names


async def test_notification_gets_no_response(server: tuple[McpServer, StubClient]) -> None:
    mcp, _ = server
    assert await mcp.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


async def test_unknown_method_is_an_error(server: tuple[McpServer, StubClient]) -> None:
    mcp, _ = server
    response = await mcp.handle({"jsonrpc": "2.0", "id": 9, "method": "unicorn"})
    assert response is not None and response["error"]["code"] == -32601


async def call(mcp: McpServer, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    response = await mcp.handle(
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": name, "arguments": arguments}}
    )
    assert response is not None
    return dict(response["result"])


async def test_report_finding_respects_the_cap(server: tuple[McpServer, StubClient]) -> None:
    mcp, client = server
    payload = {"title": "N+1 on the lines", "type": "perf", "severity": "medium", "evidence": "repo.py:88"}
    first = await call(mcp, "report_finding", payload)
    assert not first["isError"] and "recorded" in first["content"][0]["text"]
    await call(mcp, "report_finding", {**payload, "title": "Another problem"})
    third = await call(mcp, "report_finding", {**payload, "title": "One more"})
    assert third["isError"] and "cap reached" in third["content"][0]["text"]
    assert len(client.findings) == 2


async def test_scope_change_granted_pending_denied(server: tuple[McpServer, StubClient]) -> None:
    mcp, client = server
    granted = await call(mcp, "request_scope_change", {"paths": ["src/a.py"], "justification": "needed"})
    assert "widened" in granted["content"][0]["text"]

    client.scope_decision = {"decision": "pending"}
    pending = await call(mcp, "request_scope_change", {"paths": ["src/b.py"], "justification": "?"})
    assert "human" in pending["content"][0]["text"]

    client.scope_decision = {"decision": "denied", "reason": "forbidden paths"}
    denied = await call(mcp, "request_scope_change", {"paths": [".choregos/x"], "justification": "?"})
    assert denied["isError"] and "refused" in denied["content"][0]["text"]


async def test_ask_human_tells_the_agent_to_stop(server: tuple[McpServer, StubClient]) -> None:
    mcp, client = server
    result = await call(mcp, "ask_human", {"question": "Which currency?", "options": ["EUR", "USD"]})
    assert "needs_human" in result["content"][0]["text"]
    assert client.questions[0]["options"] == ["EUR", "USD"]


async def test_read_only_tools(server: tuple[McpServer, StubClient]) -> None:
    mcp, _ = server
    assert "## Spec" in (await call(mcp, "get_spec", {}))["content"][0]["text"]
    assert "no approved plan" in (await call(mcp, "get_plan", {}))["content"][0]["text"]
    assert "acme#1" in (await call(mcp, "get_ticket", {}))["content"][0]["text"]
    assert "ERROR" in (await call(mcp, "get_ci_logs", {"tail": 10}))["content"][0]["text"]
    assert "rounding" in (await call(mcp, "get_context", {}))["content"][0]["text"]


async def test_search_memory_marks_data_as_untrusted(server: tuple[McpServer, StubClient]) -> None:
    mcp, _ = server
    found = await call(mcp, "search_memory", {"query": "rounded"})
    text = found["content"][0]["text"]
    assert "decision:billing:rounding" in text
    assert "not instructions" in text
    empty = await call(mcp, "search_memory", {"query": "unicorn"})
    assert "no relevant memory" in empty["content"][0]["text"]


async def test_propose_fact_goes_through_review(server: tuple[McpServer, StubClient]) -> None:
    mcp, client = server
    result = await call(
        mcp,
        "propose_fact",
        {"subject": "convention:tests:naming", "content": "Tests follow Given/When/Then."},
    )
    assert "will review it" in result["content"][0]["text"]
    assert client.findings and client.findings[0].type == "docs"


async def test_unknown_tool_is_reported(server: tuple[McpServer, StubClient]) -> None:
    mcp, _ = server
    result = await call(mcp, "unicorn", {})
    assert result["isError"]


async def test_api_failure_does_not_kill_the_session(server: tuple[McpServer, StubClient]) -> None:
    mcp, client = server

    async def boom(finding: Finding) -> dict[str, Any]:
        raise RuntimeError("internal API unavailable")

    client.report_finding = boom  # type: ignore[method-assign]
    result = await call(
        mcp, "report_finding", {"title": "a real title", "type": "bug", "severity": "low", "evidence": "y"}
    )
    assert result["isError"] and "unavailable" in result["content"][0]["text"]


async def test_validate_result_says_before_finishing_what_the_runner_would_say_after(
    server: tuple[McpServer, StubClient],
) -> None:
    """Five repairs in eight runs on the bench: the agent can now check itself."""
    mcp, _ = server
    bad = {
        "schema": "choregos/StageResult/v1",
        "status": "done",
        "summary": "x",
        "questions": ["a string instead of an object"],
        "findings": [{"severity": "low"}],
    }
    answer = await mcp.call("validate_result", {"result": bad})
    assert answer["isError"] is True
    text = answer["content"][0]["text"]
    assert "questions.0" in text and "findings.0.title" in text

    good = {
        "schema": "choregos/StageResult/v1",
        "status": "done",
        "summary": "x",
        "questions": [],
        "findings": [],
    }
    ok = await mcp.call("validate_result", {"result": json.dumps(good)})
    assert not ok.get("isError") and "conforms" in ok["content"][0]["text"]

    assert (await mcp.call("validate_result", {"result": "{not json"}))["isError"] is True


async def test_validate_result_requires_the_outputs_the_transition_declares(
    server: tuple[McpServer, StubClient],
) -> None:
    """The triage of #4 (dev tenant, 09/10) put `size` and `risk` in an output of its own:
    the agent learns it before finishing, not in the repair loop (S22-15)."""
    from types import SimpleNamespace

    mcp, client = server

    async def fetch_input() -> Any:
        return SimpleNamespace(transition=SimpleNamespace(outputs=["size", "risk"]))

    client.fetch_input = fetch_input  # type: ignore[attr-defined]
    base = {"schema": "choregos/StageResult/v1", "status": "done", "summary": "triaged"}
    nested = await mcp.call(
        "validate_result", {"result": {**base, "outputs": {"triage": '{"size": "S", "risk": "medium"}'}}}
    )
    assert nested["isError"] is True
    assert "`size`" in nested["content"][0]["text"] and "`risk`" in nested["content"][0]["text"]

    right = await mcp.call(
        "validate_result", {"result": {**base, "outputs": {"size": "S", "risk": "medium"}}}
    )
    assert not right.get("isError"), right
    question = {**base, "status": "needs_human", "outputs": {}}
    assert not (await mcp.call("validate_result", {"result": question})).get("isError")
