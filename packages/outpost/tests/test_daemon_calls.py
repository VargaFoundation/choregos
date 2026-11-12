# SPDX-License-Identifier: Apache-2.0
"""A call made end to end: polled, checked, made against the outpost's own server, answered."""

from __future__ import annotations

import io
from pathlib import Path

from choregos_contracts.outpost import arguments_sha256
from choregos_outpost.daemon import Exit

from ._bench import CREDENTIAL, bench


async def test_a_read_is_polled_made_and_answered_sealed(tmp_path: Path, daemon_log: io.StringIO) -> None:
    b = await bench(tmp_path, daemon_log)
    call = b.door.submit(
        "tracker", "search_issues", {"query": "invoice"}, asserted={"org": "acme", "run": "r-1"}
    )
    await b.start()
    answer = await b.door.answered(call.id)
    assert await b.stop() is Exit.STOPPED
    assert answer.outcome == "result" and answer.write is False and answer.replayed is False
    assert answer.result is not None and answer.result["content"][0]["text"] == "search_issues done"
    assert b.server.calls == [("search_issues", {"query": "invoice"})]
    # The credential is the outpost's: it went to its own server, and nowhere else.
    assert all(headers["authorization"] == f"Bearer {CREDENTIAL}" for _, headers, _ in b.server.received)
    received, executed = b.lines(call.id)
    assert received["event"] == "received" and executed["event"] == "executed"
    assert received["signed_by"] == b.door.public_key == executed["signed_by"]
    assert (received["kind"], received["endpoint"], received["tool"]) == (
        "call_tool",
        "tracker",
        "search_issues",
    )
    assert received["arguments_sha256"] == arguments_sha256({"query": "invoice"})
    assert received["asserted"] == {"org": "acme", "run": "r-1"}
    assert executed["outcome"] == "result" and executed["write"] is False and executed["duration_ms"] >= 0
    assert not b.door.alerts, "one poll at a time"


async def test_a_write_is_made_where_the_endpoint_allows_writes(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")
    call = b.door.submit("tracker", "create_issue", {"title": "Invoice export fails"})
    await b.start()
    answer = await b.door.answered(call.id)
    await b.stop()
    # Whether it writes is what the outpost read from its own server: the tool declares nothing.
    assert answer.outcome == "result" and answer.write is True
    assert b.server.calls == [("create_issue", {"title": "Invoice export fails"})]
    assert [line["event"] for line in b.lines(call.id)] == ["received", "executed"]
    assert b.lines(call.id)[1]["write"] is True


async def test_a_listing_shows_what_the_policy_lets_through(tmp_path: Path, daemon_log: io.StringIO) -> None:
    b = await bench(tmp_path, daemon_log)
    call = b.door.submit("tracker")
    await b.start()
    answer = await b.door.answered(call.id)
    await b.stop()
    assert answer.outcome == "result" and answer.result is None and answer.tools is not None
    # Writes are denied by default: a tool that does not declare itself read-only is not shown, and
    # `delete_project` is outside the patterns whatever it declares.
    assert [tool.name for tool in answer.tools] == ["search_issues", "get_issue"]
    assert all(tool.read_only for tool in answer.tools)
    assert b.server.calls == [], "a listing calls no tool"
    assert [line["event"] for line in b.lines(call.id)] == ["received", "executed"]
    assert b.lines(call.id)[0]["kind"] == "list_tools" and "tool" not in b.lines(call.id)[0]


async def test_a_listing_shows_the_writes_an_endpoint_allows(tmp_path: Path, daemon_log: io.StringIO) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")
    call = b.door.submit("tracker")
    await b.start()
    answer = await b.door.answered(call.id)
    await b.stop()
    assert answer.tools is not None
    assert [(tool.name, tool.read_only) for tool in answer.tools] == [
        ("search_issues", True),
        ("get_issue", True),
        ("create_issue", False),
    ]


async def test_a_servers_own_error_is_reported_as_its_error(tmp_path: Path, daemon_log: io.StringIO) -> None:
    b = await bench(tmp_path, daemon_log, tools=["*"], writes="allow")
    call = b.door.submit("tracker", "no_such_tool", {})
    await b.start()
    answer = await b.door.answered(call.id)
    await b.stop()
    # The server answered a JSON-RPC error: it says it did not do the call.
    assert answer.outcome == "error" and answer.result is None and answer.error is not None
    assert answer.error.code == -32602 and "unknown tool" in answer.error.message
    assert b.lines(call.id)[1]["outcome"] == "error"
