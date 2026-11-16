# SPDX-License-Identifier: Apache-2.0
"""Deny by default (ADR 0147 §6): what the policy file refuses, the outpost's server never receives.

Each refusal is one code of the contract's closed list and one line of the local audit log. Whether
a tool writes is read from the outpost's own server, never from the call.
"""

from __future__ import annotations

import asyncio
import io
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts.enums import OutpostRefusalCode
from choregos_outpost.daemon import Exit

from ._bench import Bench, bench

HANDSHAKE = ["initialize", "notifications/initialized"]


async def _refused(b: Bench, call_id: str) -> dict[str, Any]:
    """Runs the daemon until the door holds a refusal of this call; returns its audit line."""
    await b.start()
    code = await b.door.refused(call_id)
    assert await b.stop() is Exit.STOPPED
    assert code in set(OutpostRefusalCode), "a refusal is one code of the closed list"
    lines = b.lines(call_id)
    assert [line["event"] for line in lines] == ["received", "refused"], lines
    assert lines[1]["refusal"] == code and "outcome" not in lines[1]
    assert call_id not in b.door.answers
    return lines[1]


async def test_an_unknown_alias_is_refused(tmp_path: Path, daemon_log: io.StringIO) -> None:
    b = await bench(tmp_path, daemon_log)
    call = b.door.submit("warehouse", "search_issues", {"query": "x"})
    line = await _refused(b, call.id)
    assert line["refusal"] == "unknown_endpoint" and line["endpoint"] == "warehouse"
    assert b.server.received == [], "the server received nothing"


async def test_a_tool_outside_its_patterns_is_refused(tmp_path: Path, daemon_log: io.StringIO) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")
    call = b.door.submit("tracker", "delete_project", {"key": "BILL"})
    line = await _refused(b, call.id)
    assert line["refusal"] == "tool_not_allowed" and line["tool"] == "delete_project"
    assert b.server.received == [], "the server received nothing"


async def test_a_tool_its_server_does_not_declare_read_only_is_refused_where_writes_are_denied(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)  # `writes` is `deny` unless the file says otherwise
    assert b.policy.endpoints["tracker"].writes == "deny"
    call = b.door.submit("tracker", "create_issue", {"title": "Invoice export fails"})
    line = await _refused(b, call.id)
    assert line["refusal"] == "write_denied" and line["write"] is True
    # The outpost asked its own server what the tool declares, and sent it no call.
    assert b.called() == [*HANDSHAKE, "tools/list", "tools/list"]
    assert b.server.calls == []


async def test_whether_a_tool_writes_is_never_read_from_the_call(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    # A control plane that says "this one is read-only" changes nothing: the names it asserts are not
    # what the outpost checks.
    call = b.door.submit(
        "tracker",
        "create_issue",
        {"title": "x"},
        read_only=True,
        write=False,
        annotations={"readOnlyHint": True},
    )
    line = await _refused(b, call.id)
    assert line["refusal"] == "write_denied"
    assert b.server.calls == []


async def test_a_tool_its_server_does_not_list_is_a_write(tmp_path: Path, daemon_log: io.StringIO) -> None:
    b = await bench(tmp_path, daemon_log, tools=["*"])
    call = b.door.submit("tracker", "undeclared_tool", {})
    line = await _refused(b, call.id)
    assert line["refusal"] == "write_denied", "without a declaration a tool is taken for a write"
    assert b.server.calls == []


async def test_a_payload_above_its_ceiling_is_refused(tmp_path: Path, daemon_log: io.StringIO) -> None:
    b = await bench(tmp_path, daemon_log, max_payload_kb=1)
    call = b.door.submit("tracker", "search_issues", {"query": "x" * 2048})
    line = await _refused(b, call.id)
    assert line["refusal"] == "payload_too_large"
    assert b.server.received == [], "the server received nothing"


async def test_a_payload_at_its_ceiling_is_made(tmp_path: Path, daemon_log: io.StringIO) -> None:
    b = await bench(tmp_path, daemon_log, max_payload_kb=1)
    # `{"query":"…"}` weighs 12 bytes around its text: 1012 characters make 1024 bytes, the ceiling.
    call = b.door.submit("tracker", "search_issues", {"query": "x" * 1012})
    await b.start()
    assert (await b.door.answered(call.id)).outcome == "result"
    await b.stop()


async def test_a_kind_the_daemon_does_not_know_is_refused_by_name(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    call = b.door.submit("tracker", kind="run_stage")
    line = await _refused(b, call.id)
    assert line["refusal"] == "unsupported_kind" and line["kind"] == "run_stage"
    assert b.server.received == []


def _at(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


async def test_a_deadline_that_is_behind_is_judged_by_the_daemons_own_clock(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    past = b.door.submit(
        "tracker", "search_issues", {"query": "x"}, deadline=_at(datetime.now(UTC) - timedelta(seconds=1))
    )
    line = await _refused(b, past.id)
    assert line["refusal"] == "deadline_passed"
    assert b.server.received == []


async def test_a_deadline_written_to_the_millisecond_is_read_to_the_millisecond(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    """#1641 (S65-23): the door writes a deadline with its fraction of a second. Under a second ahead, a
    deadline cut to the second would already be behind; read to the millisecond, the call is made."""
    b = await bench(tmp_path, daemon_log)
    ahead = datetime.now(UTC) + timedelta(milliseconds=900)
    written = ahead.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    assert written.endswith("Z") and "." in written
    call = b.door.submit("tracker", "search_issues", {"query": "x"}, deadline=written)
    await b.start()
    assert (await b.door.answered(call.id)).outcome == "result"
    await b.stop()
    assert b.server.calls == [("search_issues", {"query": "x"})]


async def test_the_doors_time_brings_a_deadline_closer_and_never_pushes_it_away(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    # The door's clock, which is not signed, says it is 50 seconds later than this machine thinks: a
    # deadline 30 seconds ahead on this machine's clock is behind on the door's, and the call is not made.
    b.door.skew(50)
    soon = b.door.submit(
        "tracker", "search_issues", {"query": "x"}, deadline=_at(datetime.now(UTC) + timedelta(seconds=30))
    )
    line = await _refused(b, soon.id)
    assert line["refusal"] == "deadline_passed"

    # The other way round it changes nothing: a door that says it is earlier does not revive a
    # deadline this machine's clock has passed.
    other = await bench(tmp_path / "other", daemon_log) if (tmp_path / "other").mkdir() is None else None
    assert other is not None
    other.door.skew(-50)
    past = other.door.submit(
        "tracker", "search_issues", {"query": "x"}, deadline=_at(datetime.now(UTC) - timedelta(seconds=5))
    )
    line = await _refused(other, past.id)
    assert line["refusal"] == "deadline_passed"
    assert other.server.received == []


async def test_beyond_the_concurrency_ceiling_a_call_is_refused_busy(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    assert b.policy.concurrency == 4
    b.gate = asyncio.Event()
    held = [b.door.submit("tracker", "search_issues", {"query": str(n)}) for n in range(4)]
    await b.start()
    await b.reached(4)
    # Four calls are in progress: the ceiling. The door hands over no more than a poll's capacity, so
    # the fifth is put in a poll's answer as a door gone wrong, or whoever replays a call, would put it.
    b.door.hand_as_is(b.handed("one-too-many"))
    assert await b.door.polled() is None
    await asyncio.wait_for(_line_of(b, "one-too-many", "refused"), timeout=5)
    b.gate.set()
    for call in held:
        assert (await b.door.answered(call.id)).outcome == "result"
    await b.stop()
    lines = b.lines("one-too-many")
    assert [line["event"] for line in lines] == ["received", "refused"] and lines[1]["refusal"] == "busy"
    assert sorted(b.arrived) == ["search_issues"] * 4, "the fifth never reached the server"
    # A poll says how many more calls the daemon can take: none while four are in progress.
    capacities = [json.loads(body)["capacity"] for verb, _, body in b.door.received if verb == "poll"]
    assert capacities[0] == 4 and 0 in capacities


async def _line_of(b: Bench, call_id: str, event: str) -> None:
    while not any(line["event"] == event for line in b.lines(call_id)):
        await asyncio.sleep(0.01)


async def test_a_refusal_code_never_names_the_rule(tmp_path: Path, daemon_log: io.StringIO) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")
    call = b.door.submit("tracker", "delete_project", {"key": "BILL"})
    await _refused(b, call.id)
    # What crossed the door for this refusal is a code and nothing else (ADR 0147 §10).
    bodies = [body for verb, _, body in b.door.received if verb == "refuse"]
    assert bodies == ['{"code":"tool_not_allowed"}']


@pytest.mark.parametrize(
    ("missing", "code"),
    [("credential", "endpoint_unavailable"), ("server", "endpoint_unavailable")],
)
async def test_an_endpoint_the_outpost_cannot_open_is_refused_and_sent_nothing(
    tmp_path: Path, daemon_log: io.StringIO, missing: str, code: str
) -> None:
    b = await bench(tmp_path, daemon_log)
    if missing == "credential":
        (tmp_path / "tracker-token").unlink()
    else:
        b.server.token = "another-credential"  # the server refuses the outpost's: 401 at `initialize`
    call = b.door.submit("tracker", "search_issues", {"query": "x"})
    line = await _refused(b, call.id)
    assert line["refusal"] == code
    assert b.server.calls == []
