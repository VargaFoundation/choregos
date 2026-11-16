# SPDX-License-Identifier: Apache-2.0
"""The tool host: the outpost's own server, called with the MCP client of `choregos_adapters`.

The credential is the outpost's and stays there; the client reaches the endpoint's own host and no
other; an answer is read up to a bound; and what a failure means depends on whether the tool call
had left when it happened.
"""

from __future__ import annotations

import asyncio
import gzip
import io
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.mcp import McpClient
from choregos_contracts.enums import OutpostAnswerOutcome
from choregos_contracts.outpost import OutpostPolicy
from choregos_core.egress import EgressRefusedError
from choregos_core.redaction import remember_secret
from choregos_outpost import logs, toolhost
from choregos_outpost.toolhost import EndpointUnavailableError, ResultTooLargeError, ToolHost

from ._bench import CREDENTIAL, TOOLS, bench


def _policy(tmp_path: Path, **endpoint: Any) -> OutpostPolicy:
    secret = tmp_path / "secret"
    secret.write_text(CREDENTIAL + "\n", encoding="utf-8")
    return OutpostPolicy.model_validate(
        {
            "control_plane": "https://outposts.example.com",
            "door_keys": ["3k9fZti-P3Z9Du9RmNmJm-pNxgUQSHfGN_bx7DmXwNw"],
            "endpoints": {
                "tracker": {
                    "url": "https://tracker.internal.example:8443/mcp",
                    "auth": {"from_file": str(secret)},
                    "tools": ["*"],
                    **endpoint,
                }
            },
            "audit": {"file": str(tmp_path / "audit.jsonl")},
        }
    )


def _serving(handler: Any) -> Any:
    return lambda: httpx.MockTransport(handler)


async def test_the_tool_host_uses_the_adapters_mcp_client_as_it_is() -> None:
    """ADR 0149 §3: nothing outside the client's own package and the platform-run plugin servers
    extends the MCP client. The tool host builds one, and defines no class of its own on it."""
    assert toolhost.McpClient is McpClient
    assert not [cls for cls in McpClient.__subclasses__() if cls.__module__.startswith("choregos_outpost")]


@pytest.mark.parametrize(
    ("header", "sent"),
    [("Authorization", f"Bearer {CREDENTIAL}"), ("X-API-Key", CREDENTIAL)],
    ids=["bearer", "raw"],
)
async def test_the_credential_is_read_from_the_file_the_policy_names(
    tmp_path: Path, header: str, sent: str
) -> None:
    server = FakeMcpServer(tools=TOOLS)
    policy = _policy(tmp_path, auth={"header": header, "from_file": str(tmp_path / "secret")})
    made = await ToolHost(policy, transport=server.transport).call(
        "tracker", "get_issue", {"key": "B-1"}, within_s=5
    )
    assert made.outcome is OutpostAnswerOutcome.RESULT and made.result is not None and made.duration_ms >= 0
    assert made.result["content"][0]["text"] == "get_issue done"
    assert all(headers[header.lower()] == sent for _, headers, _ in server.received)
    assert all(headers["user-agent"].startswith("choregos-outpost/") for _, headers, _ in server.received)
    # A rotated credential is picked up at the next call, without a restart.
    (tmp_path / "secret").write_text("rotated-credential-of-the-outpost", encoding="utf-8")
    await ToolHost(policy, transport=server.transport).listing("tracker", within_s=5)
    assert "rotated-credential-of-the-outpost" in server.received[-1][1][header.lower()]


@pytest.mark.parametrize("content", [None, "", "  \n"], ids=["absent", "empty", "blank"])
async def test_without_its_credential_an_endpoint_is_unavailable_and_sent_nothing(
    tmp_path: Path, content: str | None
) -> None:
    server = FakeMcpServer(tools=TOOLS)
    policy = _policy(tmp_path)
    if content is None:
        (tmp_path / "secret").unlink()
    else:
        (tmp_path / "secret").write_text(content, encoding="utf-8")
    with pytest.raises(EndpointUnavailableError, match="credential file"):
        await ToolHost(policy, transport=server.transport).call("tracker", "get_issue", {}, within_s=5)
    assert server.received == []


def test_a_call_may_reach_the_endpoints_own_host_and_port_and_no_other(tmp_path: Path) -> None:
    only = toolhost._only(_policy(tmp_path).endpoints["tracker"])
    assert only.mode == "enforce"
    only.check("tracker.internal.example", 8443)
    for host, port in (
        ("tracker.internal.example", 443),
        ("elsewhere.example", 8443),
        ("169.254.169.254", 80),
    ):
        with pytest.raises(EgressRefusedError):
            only.check(host, port)


async def test_a_server_that_redirects_elsewhere_is_not_followed_there(tmp_path: Path) -> None:
    seen: list[str] = []

    def redirecting(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(307, headers={"Location": "https://elsewhere.example/mcp"})

    with pytest.raises(EndpointUnavailableError):
        await ToolHost(_policy(tmp_path), transport=_serving(redirecting)).call(
            "tracker", "get_issue", {}, within_s=5
        )
    assert set(seen) == {"tracker.internal.example"}


def _answering(tool_call: Any) -> Any:
    """A server whose handshake is ordinary, and whose answer to `tools/call` is the test's."""
    server = FakeMcpServer(tools=TOOLS)

    async def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("method") == "tools/call":
            answer = tool_call(body)
            return await answer if asyncio.iscoroutine(answer) else answer
        return server.respond(request)

    return _serving(respond)


async def test_a_json_rpc_error_says_the_server_did_not_do_the_call(tmp_path: Path) -> None:
    def refuse(body: dict[str, Any]) -> httpx.Response:
        error = {"code": -32602, "message": "Invalid params: `title` is required"}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": error})

    made = await ToolHost(_policy(tmp_path), transport=_answering(refuse)).call(
        "tracker", "create_issue", {}, within_s=5
    )
    assert made.outcome is OutpostAnswerOutcome.ERROR and made.error is not None
    assert (made.error.code, made.error.message) == (
        -32602,
        "tools/call: Invalid params: `title` is required",
    )


@pytest.mark.parametrize(
    "answer",
    [
        lambda body: httpx.Response(500, text="upstream timeout"),
        lambda body: httpx.Response(502, json={"error": "bad gateway"}),
        lambda body: httpx.Response(200, text="<html>not what was asked</html>"),
        lambda body: httpx.Response(
            200, json={"jsonrpc": "2.0", "id": body["id"], "error": {"message": "no code"}}
        ),
    ],
    ids=["a 500", "a 502", "an answer nobody can read", "an error without the protocol's code"],
)
async def test_anything_else_after_the_call_left_is_unanswered(tmp_path: Path, answer: Any) -> None:
    """Executed or not, nobody knows: only a JSON-RPC error says the call was not made."""
    made = await ToolHost(_policy(tmp_path), transport=_answering(answer)).call(
        "tracker", "create_issue", {}, within_s=5
    )
    assert made.outcome is OutpostAnswerOutcome.UNANSWERED and made.result is None and made.error is None


async def test_a_server_that_does_not_answer_in_time_is_unanswered(tmp_path: Path) -> None:
    async def never(_body: dict[str, Any]) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    host = ToolHost(_policy(tmp_path, timeout_s=1), transport=_answering(never))
    made = await host.call("tracker", "create_issue", {}, within_s=0.05)
    assert made.outcome is OutpostAnswerOutcome.UNANSWERED and made.duration_ms < 2000


async def test_a_failure_before_the_call_left_is_the_endpoint_unavailable(tmp_path: Path) -> None:
    def down(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(EndpointUnavailableError):
        await ToolHost(_policy(tmp_path), transport=_serving(down)).call(
            "tracker", "create_issue", {}, within_s=5
        )

    async def slow_handshake(_request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(30)
        return httpx.Response(200)

    with pytest.raises(EndpointUnavailableError):
        await ToolHost(_policy(tmp_path), transport=_serving(slow_handshake)).call(
            "tracker", "create_issue", {}, within_s=0.05
        )


async def test_a_result_above_the_ceiling_is_oversized_and_not_carried(tmp_path: Path) -> None:
    def big(size: int) -> Any:
        def answer(body: dict[str, Any]) -> httpx.Response:
            result = {"content": [{"type": "text", "text": "x" * size}], "isError": False}
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

        return answer

    policy = _policy(tmp_path, max_payload_kb=1)
    within = await ToolHost(policy, transport=_answering(big(900))).call(
        "tracker", "get_issue", {}, within_s=5
    )
    assert within.outcome is OutpostAnswerOutcome.RESULT
    above = await ToolHost(policy, transport=_answering(big(2000))).call(
        "tracker", "get_issue", {}, within_s=5
    )
    assert above.outcome is OutpostAnswerOutcome.OVERSIZED and above.result is None
    # Far above: the answer is not even read to its end.
    far = await ToolHost(policy, transport=_answering(big(5_000_000))).call(
        "tracker", "get_issue", {}, within_s=5
    )
    assert far.outcome is OutpostAnswerOutcome.OVERSIZED and far.result is None


async def test_a_listing_above_what_the_outpost_reads_is_not_read(tmp_path: Path) -> None:
    tools = [{"name": f"tool_{n}", "description": "d" * 4000, "inputSchema": {}} for n in range(40)]
    server = FakeMcpServer(tools=tools, page_size=40)
    with pytest.raises(ResultTooLargeError):
        await ToolHost(_policy(tmp_path, max_payload_kb=1), transport=server.transport).listing(
            "tracker", within_s=5
        )


async def test_a_compressed_answer_is_bounded_once_decoded(tmp_path: Path) -> None:
    """A small body that inflates to a large one is measured as what it becomes."""

    def bomb(body: dict[str, Any]) -> httpx.Response:
        result = {"content": [{"type": "text", "text": "x" * 5_000_000}], "isError": False}
        raw = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
        packed = gzip.compress(raw)
        assert len(packed) < 16_384
        return httpx.Response(
            200, content=packed, headers={"Content-Encoding": "gzip", "Content-Type": "application/json"}
        )

    made = await ToolHost(_policy(tmp_path, max_payload_kb=1), transport=_answering(bomb)).call(
        "tracker", "get_issue", {}, within_s=5
    )
    assert made.outcome is OutpostAnswerOutcome.OVERSIZED


async def test_a_credential_a_server_echoes_in_its_error_does_not_leave_the_outpost(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")

    def echoing(body: dict[str, Any]) -> httpx.Response:
        error = {"code": -32000, "message": f"upstream said: invalid header 'Bearer {CREDENTIAL}'"}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "error": error})

    call = b.door.submit("tracker", "create_issue", {"title": "x"})
    await b.start(tools=ToolHost(b.policy, transport=_answering(echoing)))
    answer = await b.door.answered(call.id)
    await b.stop()
    assert answer.outcome == "error" and answer.error is not None
    assert CREDENTIAL not in answer.error.message and "[REDACTED]" in answer.error.message
    assert CREDENTIAL not in daemon_log.getvalue() and CREDENTIAL not in b.audit_path.read_text(
        encoding="utf-8"
    )


async def test_a_credential_in_an_error_the_daemon_logs_is_masked(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)

    def refusing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"proxy rejected Authorization: Bearer {CREDENTIAL}")

    call = b.door.submit("tracker", "search_issues", {"query": "x"})
    await b.start(tools=ToolHost(b.policy, transport=_serving(refusing)))
    assert await b.door.refused(call.id) == "endpoint_unavailable"
    await b.stop()
    said = b.logged("endpoint.unavailable")
    assert said and "[REDACTED]" in said[0]["reason"] and CREDENTIAL not in daemon_log.getvalue()


def test_every_string_of_a_log_line_is_masked_of_the_credentials_the_daemon_read(
    daemon_log: io.StringIO,
) -> None:
    """Whatever a later line carries (an exception text, a field, a nested value), a credential the
    daemon read does not reach its log."""
    remember_secret(CREDENTIAL)
    logs.get_logger().error(
        "endpoint.failed",
        reason=f"401 for Authorization: Bearer {CREDENTIAL}",
        nested={"headers": [f"X-API-Key: {CREDENTIAL}"], "status": 401},
    )
    (line,) = [json.loads(text) for text in daemon_log.getvalue().splitlines()]
    assert CREDENTIAL not in daemon_log.getvalue()
    assert line["reason"] == "401 for Authorization: Bearer [REDACTED]"
    assert line["nested"] == {"headers": ["X-API-Key: [REDACTED]"], "status": 401}
    assert line["level"] == "error" and line["event"] == "endpoint.failed" and line["timestamp"].endswith("Z")
