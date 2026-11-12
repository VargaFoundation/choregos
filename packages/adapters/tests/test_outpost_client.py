# SPDX-License-Identifier: Apache-2.0
"""The tool client of a connector at an outpost, and the fake outpost it is tested with (S65-13).

`OutpostToolClient` places a call at a rendezvous, waits for what becomes of it and reads it. What
comes back is what a direct MCP client would have given; anything else is one of the typed refusals
of ADR 0149 §7. `FakeOutpost` is the rendezvous in memory and the outpost behind it: it decides what
an outpost decides alone, from a policy the platform cannot write, and is scripted to go offline, to
refuse and to lose a lease.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.fakes.outpost import FakeOutpost
from choregos_adapters.mcp import McpClient, McpError, McpTool
from choregos_adapters.outpost import (
    OutpostInDoubt,
    OutpostNotAdmitted,
    OutpostOffline,
    OutpostPaused,
    OutpostRefusal,
    OutpostRefused,
    OutpostTimeout,
    OutpostToolClient,
    port,
)
from choregos_contracts import OutpostAnswer, OutpostCallState, OutpostRefusalCode, load_schema
from jsonschema import Draft202012Validator

TOOLS: list[dict[str, Any]] = [
    {
        "name": "search_issues",
        "description": "the issues that match",
        "annotations": {"readOnlyHint": True},
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
        "outputSchema": {"type": "object"},
    },
    {"name": "get_issue", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
    {"name": "create_issue", "description": "opens an issue", "inputSchema": {"type": "object"}},
]


def _server(**scripted: Any) -> FakeMcpServer:
    return FakeMcpServer(tools=[dict(tool) for tool in TOOLS], **scripted)


def _outpost(server: FakeMcpServer | None = None, **policy: Any) -> FakeOutpost:
    return FakeOutpost(name="factory-floor", endpoints={"tracker": server or _server()}, **policy)


def _client(outpost: Any, **carried: Any) -> OutpostToolClient:
    return OutpostToolClient(outpost, endpoint="tracker", timeout_s=5, **carried)


# ───────────────────────── what a direct client would have given ─────────────────────────


async def test_a_tool_call_gives_the_servers_result_untouched() -> None:
    server = _server(structured={"search_issues": {"issues": [{"key": "OPS-1"}]}})
    outpost = _outpost(server)
    through = await _client(outpost, asserted={"run": "r-1"}).call_tool("search_issues", {"query": "door"})
    direct = await McpClient("http://direct.test/mcp", transport=server.transport()).call_tool(
        "search_issues", {"query": "door"}
    )
    assert through == direct
    assert server.calls == [("search_issues", {"query": "door"})] * 2
    (placed,) = outpost.placed
    assert (placed.kind.value, placed.endpoint, placed.tool) == ("call_tool", "tracker", "search_issues")
    assert (placed.timeout_s, dict(placed.asserted), placed.write_key) == (5.0, {"run": "r-1"}, None)
    (received,) = outpost.received
    assert received["arguments"] == {"query": "door"} and received["asserted"] == {"run": "r-1"}
    assert "write" not in received, "the call says nothing of whether the tool writes"


async def test_a_listing_gives_the_tools_as_the_mcp_client_describes_them() -> None:
    server = _server()
    through = await _client(_outpost(server)).list_tools()
    direct = await McpClient("http://direct.test/mcp", transport=server.transport()).list_tools()
    assert through == direct and all(isinstance(tool, McpTool) for tool in through)
    assert [(tool.name, tool.read_only) for tool in through] == [
        ("search_issues", True),
        ("get_issue", True),
        ("create_issue", False),
    ]
    assert through[0].output_schema == {"type": "object"}
    assert server.calls == [], "a listing calls no tool"


async def test_a_servers_json_rpc_error_is_the_error_the_mcp_client_raises() -> None:
    # A tool its server does not list is no read the outpost knows of: the alias allows writes here.
    outpost = _outpost(writes_allowed={"tracker"})
    with pytest.raises(McpError, match="unknown tool: nothing") as through:
        await _client(outpost).call_tool("nothing", {})
    assert through.value.code == -32602
    outpost.answer_next("error", message="the tracker is read-only tonight", code=-32000)
    with pytest.raises(McpError, match="read-only tonight") as scripted:
        await _client(outpost).call_tool("search_issues", {})
    assert scripted.value.code == -32000


async def test_a_result_the_outpost_does_not_send_back_is_an_answer_that_says_so() -> None:
    """`oversized`: the call was made and its result is above what the outpost sends. It is not a
    refusal: the server executed it, and the agent is told to ask for less."""
    server = _server()
    outpost = _outpost(server)
    outpost.answer_next("oversized")
    result = await _client(outpost).call_tool("search_issues", {"query": "everything"})
    assert result["isError"] is True
    assert "factory-floor" in result["content"][0]["text"] and "ask for less" in result["content"][0]["text"]
    assert server.calls == [("search_issues", {"query": "everything"})]


# ───────────────────────── the typed refusals ─────────────────────────


def _named(refusal: OutpostRefusal, code: str, reached_no_server: bool) -> None:
    assert (refusal.code, refusal.outpost, refusal.endpoint) == (code, "factory-floor", "tracker")
    assert refusal.reached_no_server is reached_no_server
    assert "factory-floor" in str(refusal), "a refusal names the outpost"


async def test_an_offline_outpost_is_a_refusal_and_nothing_is_placed() -> None:
    server = _server()
    outpost = _outpost(server)
    outpost.go_offline()
    with pytest.raises(OutpostOffline) as refusal:
        await _client(outpost).call_tool("search_issues", {})
    _named(refusal.value, "outpost_offline", True)
    assert outpost.placed == [] and outpost.received == [] and server.calls == []
    outpost.come_back()
    assert (await _client(outpost).call_tool("search_issues", {}))["isError"] is False


async def test_a_paused_outpost_is_handed_nothing() -> None:
    outpost = _outpost()
    outpost.pause()
    with pytest.raises(OutpostPaused) as refusal:
        await _client(outpost).list_tools()
    _named(refusal.value, "outpost_paused", True)
    assert outpost.placed == []
    outpost.resume()
    assert len(await _client(outpost).list_tools()) == 3


@pytest.mark.parametrize("code", sorted(code.value for code in OutpostRefusalCode))
async def test_a_refusal_of_the_outpost_carries_its_code(code: str) -> None:
    server = _server()
    outpost = _outpost(server)
    outpost.refuse_next(code)
    with pytest.raises(OutpostRefused) as refusal:
        await _client(outpost).call_tool("search_issues", {})
    _named(refusal.value, "outpost_refused", True)
    assert refusal.value.refusal is OutpostRefusalCode(code) and code in str(refusal.value)
    assert server.calls == [], "whatever the code, the server received nothing"


async def test_the_fake_outpost_refuses_what_an_outpost_refuses_alone() -> None:
    """From its own policy and its own server, never from the call (ADR 0147 §6)."""
    server = _server()
    outpost = _outpost(server, tools={"tracker": ["search_*", "create_*"]}, max_payload_bytes=64)
    refusals = {}
    for alias, tool, arguments in (
        ("forge", "search_issues", {}),
        ("tracker", "get_issue", {}),
        ("tracker", "create_issue", {}),
        ("tracker", "search_issues", {"query": "x" * 100}),
    ):
        client = OutpostToolClient(outpost, endpoint=alias, timeout_s=5)
        with pytest.raises(OutpostRefused) as refusal:
            await client.call_tool(tool, arguments)
        refusals[(alias, tool)] = refusal.value.refusal.value
    assert refusals == {
        ("forge", "search_issues"): "unknown_endpoint",
        ("tracker", "get_issue"): "tool_not_allowed",
        ("tracker", "create_issue"): "write_denied",
        ("tracker", "search_issues"): "payload_too_large",
    }
    assert server.calls == []
    # The listing shows what the policy lets through, and no more.
    assert [tool.name for tool in await _client(outpost).list_tools()] == ["search_issues", "create_issue"]
    # A write is made where the alias allows writes, and reported as one.
    outpost.writes_allowed.add("tracker")
    assert (await _client(outpost).call_tool("create_issue", {}))["isError"] is False
    assert server.calls == [("create_issue", {})]


async def test_a_claimed_read_that_gets_no_answer_times_out_and_may_be_asked_again() -> None:
    server = _server()
    outpost = _outpost(server)
    outpost.lose_lease()
    with pytest.raises(OutpostTimeout) as refusal:
        await _client(outpost).call_tool("search_issues", {"query": "door"})
    _named(refusal.value, "outpost_timeout", False)
    assert server.calls == [("search_issues", {"query": "door"})], "the server did receive the call"
    # Asked again, it is a new call.
    assert (await _client(outpost).call_tool("search_issues", {"query": "door"}))["isError"] is False
    assert len(outpost.placed) == 2 and len(server.calls) == 2
    # An answer that says `unanswered` is the same: executed or not, nobody knows.
    outpost.answer_next("unanswered")
    with pytest.raises(OutpostTimeout):
        await _client(outpost).call_tool("search_issues", {"query": "door"})


async def test_a_claimed_write_that_gets_no_answer_is_in_doubt_and_is_never_delivered_again() -> None:
    server = _server()
    outpost = _outpost(server, writes_allowed={"tracker"})
    outpost.lose_lease()
    for _attempt in range(3):
        with pytest.raises(OutpostInDoubt) as refusal:
            await _client(outpost, write_key="action-1:0").call_tool("create_issue", {"title": "door"})
        _named(refusal.value, "outpost_in_doubt", False)
        assert refusal.value.tool == "create_issue" and refusal.value.call_id
        assert refusal.value.instance_id == "factory-floor-0" and refusal.value.claimed_at is not None
    assert len(outpost.placed) == 1, "the same key is the same call: placed once"
    assert server.calls == [("create_issue", {"title": "door"})], "and delivered once"


async def test_a_retried_write_reads_the_first_answer_and_another_key_is_another_call() -> None:
    server = _server(structured={"create_issue": {"key": "OPS-7"}})
    outpost = _outpost(server, writes_allowed={"tracker"})
    first = await _client(outpost, write_key="action-1:0").call_tool("create_issue", {"title": "door"})
    again = await _client(outpost, write_key="action-1:0").call_tool("create_issue", {"title": "door"})
    assert first == again and first["structuredContent"] == {"key": "OPS-7"}
    assert len(server.calls) == 1, "a write's answer is not forgotten by the client: a retry reads it"
    await _client(outpost, write_key="action-1:1").call_tool("create_issue", {"title": "door"})
    assert len(server.calls) == 2
    # A read is forgotten once it was read: nothing of its answer waits anywhere.
    await _client(outpost).call_tool("search_issues", {})
    (read_id,) = [call_id for call_id, placed in outpost._calls.items() if not placed.write]
    assert outpost._calls[read_id].outcome is not None and outpost._calls[read_id].outcome.answer is None
    # What the platform stops waiting for is noted, for a call the outpost knows of.
    await outpost.cancel(read_id)
    await outpost.cancel("a call that never was")
    assert outpost.cancellations == [read_id]


class _Scripted:
    """A rendezvous that does what a test says: what the client makes of each outcome."""

    outpost = "factory-floor"

    def __init__(
        self, outcome: port.Outcome | None = None, refuse: port.NotAdmitted | None = None, hang: bool = False
    ) -> None:
        self._outcome, self._refuse, self._hang = outcome, refuse, hang
        self.placed: list[port.Call] = []
        self.forgotten: list[str] = []
        self.cancelled: list[str] = []
        self.waiting = asyncio.Event()

    async def place(self, call: port.Call) -> str:
        if self._refuse is not None:
            raise self._refuse
        self.placed.append(call)
        return "call-1"

    async def wait(self, call_id: str) -> port.Outcome | None:
        if self._hang:
            self.waiting.set()
            await asyncio.Event().wait()
        return self._outcome

    async def cancel(self, call_id: str) -> None:
        self.cancelled.append(call_id)

    async def forget(self, call_id: str) -> None:
        self.forgotten.append(call_id)


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (port.SWITCHED_OFF, OutpostPaused),
        (port.PAUSED, OutpostPaused),
        (port.REVOKED, OutpostPaused),
        (port.NO_SECRETS_KEY, OutpostPaused),
        (port.OFFLINE, OutpostOffline),
        (port.OUTPOST_QUOTA, OutpostNotAdmitted),
        (port.ORGANISATION_QUOTA, OutpostNotAdmitted),
        (port.OUTPOST_RATE, OutpostNotAdmitted),
        (port.ORGANISATION_RATE, OutpostNotAdmitted),
        (port.TOO_LARGE, OutpostNotAdmitted),
        (port.ID_TAKEN, OutpostNotAdmitted),
        (port.INVALID_CALL, OutpostNotAdmitted),
        (port.WRITE_WITHOUT_KEY, OutpostNotAdmitted),
    ],
)
async def test_a_call_the_rendezvous_does_not_take_is_a_refusal_that_reached_no_server(
    reason: str, expected: type[OutpostRefusal]
) -> None:
    rendezvous = _Scripted(refuse=port.NotAdmitted(reason, "the reason, as the rendezvous says it"))
    with pytest.raises(expected) as refusal:
        await _client(rendezvous).call_tool("search_issues", {})
    assert type(refusal.value) is expected and refusal.value.reached_no_server
    assert "factory-floor" in str(refusal.value) and "as the rendezvous says it" in str(refusal.value)
    if expected is OutpostNotAdmitted:
        assert refusal.value.reason == reason  # type: ignore[attr-defined]
    assert rendezvous.forgotten == [] and rendezvous.cancelled == [], "nothing was placed"


@pytest.mark.parametrize(
    ("outcome", "write_key", "expected"),
    [
        (port.Outcome(OutpostCallState.EXPIRED), None, OutpostOffline),
        (port.Outcome(OutpostCallState.EXPIRED, write=True), "k", OutpostOffline),
        (port.Outcome(OutpostCallState.TIMED_OUT), None, OutpostTimeout),
        (port.Outcome(OutpostCallState.IN_DOUBT, write=True), "k", OutpostInDoubt),
        # Answered, and the answer is no longer held: nobody can say what it was.
        (port.Outcome(OutpostCallState.ANSWERED), None, OutpostTimeout),
        (port.Outcome(OutpostCallState.ANSWERED, write=True), "k", OutpostInDoubt),
        # A refusal without its code is no refusal anyone can read.
        (port.Outcome(OutpostCallState.REFUSED), None, OutpostTimeout),
        # No such call any more.
        (None, None, OutpostTimeout),
        (None, "k", OutpostInDoubt),
    ],
)
async def test_what_the_client_makes_of_each_outcome(
    outcome: port.Outcome | None, write_key: str | None, expected: type[OutpostRefusal]
) -> None:
    rendezvous = _Scripted(outcome=outcome)
    with pytest.raises(expected) as refusal:
        await _client(rendezvous, write_key=write_key).call_tool("create_issue", {})
    assert type(refusal.value) is expected
    # A read is forgotten once read; a write is left for whoever makes its result durable.
    assert rendezvous.forgotten == ([] if write_key else ["call-1"])
    assert rendezvous.cancelled == []


async def test_a_caller_that_gives_up_cancels_its_call() -> None:
    rendezvous = _Scripted(hang=True)
    calling = asyncio.create_task(_client(rendezvous).call_tool("search_issues", {}))
    await rendezvous.waiting.wait()
    calling.cancel()
    with pytest.raises(asyncio.CancelledError):
        await calling
    assert rendezvous.cancelled == ["call-1"] and rendezvous.forgotten == []


async def test_an_answer_that_is_not_what_was_asked_is_unreadable_not_a_result() -> None:
    listing = OutpostAnswer.model_validate({"id": "call-1", "outcome": "result", "tools": []})
    rendezvous = _Scripted(outcome=port.Outcome(OutpostCallState.ANSWERED, answer=listing))
    with pytest.raises(McpError, match="a tool's result was expected"):
        await _client(rendezvous).call_tool("search_issues", {})
    result = OutpostAnswer.model_validate({"id": "call-1", "outcome": "result", "result": {"content": []}})
    rendezvous = _Scripted(outcome=port.Outcome(OutpostCallState.ANSWERED, answer=result))
    with pytest.raises(McpError, match="a tool listing was expected"):
        await _client(rendezvous).list_tools()


# ───────────────────────── the fake speaks the contract ─────────────────────────


async def test_what_the_fake_outpost_answers_is_the_contracts_answer() -> None:
    """Whoever carries its answers to the platform seals them as they are: they must be what
    `outpost-answer.schema.json` describes, for every outcome."""
    validator = Draft202012Validator(load_schema("outpost-answer.schema.json"))
    outpost = _outpost(writes_allowed={"tracker"})

    def call(tool: str | None, kind: str = "call_tool") -> dict[str, Any]:
        base: dict[str, Any] = {"id": "call-1", "kind": kind, "endpoint": "tracker", "asserted": {}}
        return base | ({"tool": tool, "arguments": {}} if tool else {})

    answers = [await outpost.make(call(None, "list_tools")), await outpost.make(call("search_issues"))]
    answers.append(await outpost.make(call("create_issue")))
    answers.append(await outpost.make(call("nothing")))
    for outcome in ("unanswered", "oversized", "error"):
        outpost.answer_next(outcome, message="no", code=-32000)
        answers.append(await outpost.make(call("search_issues")))
    for made in answers:
        assert made.answer is not None and made.refusal is None
        assert not [error.message for error in validator.iter_errors(made.answer)], made.answer
        OutpostAnswer.model_validate(made.answer)
    assert [made.answer["outcome"] for made in answers if made.answer] == [
        "result",
        "result",
        "result",
        "error",
        "unanswered",
        "oversized",
        "error",
    ]
    assert (answers[1].answer or {})["write"] is False and (answers[2].answer or {})["write"] is True
    assert (await outpost.make({"id": "call-2", "kind": "run_a_shell"})).refusal == "unsupported_kind"
    outpost.lose_lease()
    assert not (await outpost.make(call("search_issues"))).sent


def test_every_reason_of_the_port_is_a_refusal_class() -> None:
    reasons = {value for name, value in vars(port).items() if name.isupper() and isinstance(value, str)}
    assert reasons >= port.CLOSED | port.BUSY
    assert not port.CLOSED & port.BUSY
    assert reasons - port.CLOSED - port.BUSY == {
        "offline",
        "too_large",
        "id_taken",
        "invalid_call",
        "write_without_key",
    }
