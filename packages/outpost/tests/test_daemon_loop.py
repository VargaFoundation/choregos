# SPDX-License-Identifier: Apache-2.0
"""The loop: one poll at a time, a wait that doubles with jitter, and what stops it for good."""

from __future__ import annotations

import asyncio
import io
import json
import random
from pathlib import Path

import httpx
from choregos_contracts.enums import OutpostDoorCode
from choregos_contracts.outpost import MAX_WAIT_S
from choregos_outpost.daemon import Exit
from choregos_outpost.toolhost import ToolHost
from choregos_outpost.uplink import Backoff

from ._bench import CREDENTIAL, Bench, bench


async def test_a_daemon_below_the_doors_minimum_stops_polling_and_says_so(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    b.door.serve(current=3, minimum=2)
    pending = b.door.submit("tracker", "search_issues", {"query": "x"})
    await b.start()
    assert await b.ended() is Exit.UPGRADE_REQUIRED
    said = b.logged("outpost.protocol_unsupported")
    assert len(said) == 1 and said[0]["level"] == "error"
    assert (said[0]["speaks"], said[0]["door_minimum"], said[0]["door_current"]) == (1, 2, 3)
    assert "stopped polling" in said[0]["detail"] and "Upgrade" in said[0]["detail"]
    # It got no assignment, and it does not ask again: nobody upgrades it for its operator.
    polls = b.door.polls
    await asyncio.sleep(0.2)
    assert b.door.polls == polls and pending.state == "pending" and b.server.received == []


async def test_a_window_above_the_daemon_in_an_ordinary_answer_stops_it_too(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    """The window is announced in every poll's answer: a daemon does not wait for the 426."""
    b = await bench(tmp_path, daemon_log)

    async def announcing(request: httpx.Request) -> httpx.Response:
        response = await b.door.handle(request)
        if response.status_code == 200 and request.url.path.endswith("/poll"):
            document = response.json() | {"protocol": {"current": 4, "minimum": 2}}
            return httpx.Response(
                200, json=document, headers={"Outpost-Nonce": response.headers["Outpost-Nonce"]}
            )
        return response

    await b.start(transport=httpx.MockTransport(announcing))
    assert await b.ended() is Exit.UPGRADE_REQUIRED
    assert b.logged("outpost.protocol_unsupported")[0]["door_minimum"] == 2


async def test_an_unreachable_door_is_asked_again_after_a_wait_that_doubles_with_jitter(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    failures = [6]
    naps: list[float] = []

    async def flaky(request: httpx.Request) -> httpx.Response:
        if failures[0] > 0:
            failures[0] -= 1
            raise httpx.ConnectError("connection refused")
        return await b.door.handle(request)

    async def nap(seconds: float) -> None:
        naps.append(seconds)
        await asyncio.sleep(0)

    call = b.door.submit("tracker", "search_issues", {"query": "x"})
    await b.start(transport=httpx.MockTransport(flaky), backoff=Backoff(rng=random.Random(3)), sleep=nap)
    await b.door.answered(call.id)
    await b.stop()
    waits = naps[:6]
    for wait, ceiling in zip(waits, [1, 2, 4, 8, 16, 32], strict=True):
        assert ceiling / 2 <= wait <= ceiling, waits
    assert len(set(waits)) == 6, "no two waits are the same"
    assert [line["event"] for line in b.logged("door.unreachable")] == ["door.unreachable"] * 6
    # Once the door answers, the next failure starts from the short wait again.
    assert b.daemon is not None and b.daemon.backoff.attempt == 0


async def test_a_revoked_instance_stops(tmp_path: Path, daemon_log: io.StringIO) -> None:
    b = await bench(tmp_path, daemon_log)
    await b.start()
    await b.door.polled()
    b.door.revoke(b.instance_id)
    assert await b.ended() is Exit.REVOKED
    assert b.logged("outpost.revoked")
    polls = b.door.polls
    await asyncio.sleep(0.1)
    assert b.door.polls == polls


async def test_a_pending_instance_keeps_asking_and_says_which_fingerprint_to_compare(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log, approved=False)

    async def nap(_seconds: float) -> None:
        await asyncio.sleep(0.01)

    call = b.door.submit("tracker", "search_issues", {"query": "x"})
    await b.start(sleep=nap)
    await b.door.polled(3)
    assert call.state == "pending", "a pending instance is handed nothing"
    said = b.logged("outpost.pending")
    assert len(said) == 1 and said[0]["fingerprint"] == b.identity.fingerprint
    b.door.approve(b.instance_id, b.identity.fingerprint)
    assert (await b.door.answered(call.id)).outcome == "result"
    await b.stop()


async def test_a_paused_outpost_keeps_polling_and_finishes_what_it_holds(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    b.gate = asyncio.Event()
    held = b.door.submit("tracker", "search_issues", {"query": "held"})
    await b.start()
    await b.reached()
    b.door.pause()
    waiting = b.door.submit("tracker", "search_issues", {"query": "later"})
    await b.door.polled(3)
    b.gate.set()
    assert (await b.door.answered(held.id)).outcome == "result"
    assert waiting.state == "pending" and b.logged("outpost.paused")
    b.door.pause(False)
    assert (await b.door.answered(waiting.id)).outcome == "result"
    await b.stop()
    assert b.logged("outpost.resumed")


async def test_the_poll_says_what_the_daemon_serves_can_take_and_holds(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    b.gate = asyncio.Event()
    call = b.door.submit("tracker", "search_issues", {"query": "x"})
    await b.start()
    await b.reached()
    await b.door.polled(2)
    b.gate.set()
    await b.door.answered(call.id)
    await b.stop()
    polls = [json.loads(body) for verb, claims, body in b.door.received if verb == "poll" and claims]
    assert polls[0] == {
        "protocol": 1,
        "daemon_version": polls[0]["daemon_version"],
        "wait_s": MAX_WAIT_S,
        "capacity": 4,
        "capabilities": ["tools"],
        "endpoints": ["tracker"],
        "running": [],
    }
    # Names only: no address and no credential of the outpost's side crosses the door (ADR 0147 §7).
    crossed = " ".join(body for _, _, body in b.door.received)
    assert "tracker-mcp" not in crossed and CREDENTIAL not in crossed and "tracker-token" not in crossed
    during = next(poll for poll in polls if poll["running"])
    assert during["running"] == [call.id] and during["capacity"] == 3
    assert not b.door.alerts, "one poll at a time: the door never saw two"


async def _refusal(b: Bench, call_id: str) -> str:
    async with asyncio.timeout(5):
        while not any(line["event"] == "refused" for line in b.lines(call_id)):
            await asyncio.sleep(0.01)
    return str(b.lines(call_id)[-1]["refusal"])


async def test_a_call_cancelled_before_it_started_is_refused_and_its_server_receives_nothing(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    listing = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        if json.loads(request.content).get("method") == "tools/list":
            await listing.wait()  # the outpost is still asking its server what the tool declares
        return b.server.respond(request)

    call = b.door.submit("tracker", "search_issues", {"query": "x"})
    await b.start(tools=ToolHost(b.policy, transport=lambda: httpx.MockTransport(slow)))
    await b.door.polled(2)
    b.door.cancel(call.id)
    assert await _refusal(b, call.id) == "cancelled"
    listing.set()
    await b.stop()
    assert b.server.calls == [] and call.id not in b.door.answers


async def test_a_read_in_flight_is_abandoned_when_the_door_cancels_it(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    b.gate = asyncio.Event()
    call = b.door.submit("tracker", "search_issues", {"query": "x"})
    await b.start()
    await b.reached()
    b.door.cancel(call.id)
    async with asyncio.timeout(5):
        while len(b.lines(call.id)) < 2:
            await asyncio.sleep(0.01)
    await b.stop()
    assert [(line["event"], line.get("outcome")) for line in b.lines(call.id)] == [
        ("received", None),
        ("executed", "unanswered"),
    ]
    assert call.id not in b.door.answers and call.id not in b.door.refusals


async def test_a_write_that_has_begun_is_left_to_finish_when_the_door_cancels_it(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")
    b.gate = asyncio.Event()
    call = b.door.submit("tracker", "create_issue", {"title": "x"})
    await b.start()
    await b.reached()
    b.door.cancel(call.id)
    await b.door.polled(2)
    assert b.logged("call.cancel_too_late")
    b.gate.set()
    async with asyncio.timeout(5):
        while len(b.lines(call.id)) < 2:
            await asyncio.sleep(0.01)
    await b.stop()
    # What it did is in the operator's log, and in the journal; the door, which waits no more, drops
    # the answer (`lease_lost`).
    assert b.lines(call.id)[1]["outcome"] == "result" and b.lines(call.id)[1]["write"] is True
    assert b.server.calls == [("create_issue", {"title": "x"})]
    assert call.id not in b.door.answers


async def test_a_stop_lets_what_the_daemon_holds_finish_and_answer(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")
    b.gate = asyncio.Event()
    call = b.door.submit("tracker", "create_issue", {"title": "x"})
    await b.start(grace_s=5.0)
    await b.reached()
    assert b.daemon is not None and b.task is not None
    b.daemon.stop()
    await asyncio.sleep(0.05)
    assert not b.task.done(), "the write is in flight: the daemon waits for it"
    b.gate.set()
    assert await b.stop() is Exit.STOPPED
    assert b.door.answers[call.id].outcome == "result"


async def test_an_answer_the_door_did_not_take_is_sent_again(tmp_path: Path, daemon_log: io.StringIO) -> None:
    """A refusal or an answer that never arrived would leave a write in doubt at the platform."""
    b = await bench(tmp_path, daemon_log, writes="allow")

    async def nap(_seconds: float) -> None:
        await asyncio.sleep(0.01)

    b.door.refuse_next("answer", OutpostDoorCode.RATE_LIMITED, times=2)
    call = b.door.submit("tracker", "create_issue", {"title": "x"})
    await b.start(sleep=nap)
    assert (await b.door.answered(call.id)).outcome == "result"
    await b.stop()
    assert b.server.calls == [("create_issue", {"title": "x"})], "sent again, never made again"
    sent = [claims for verb, claims, _ in b.door.received if verb == "answer" and claims]
    assert len(sent) == 3


async def test_more_calls_than_the_contract_allows_in_one_answer_are_not_the_doors(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    """A poll's answer holds 64 calls at most (S65-10b): one that carries more is not the contract's,
    so none of it is taken, not even its first call, and the daemon says so and asks again."""
    b = await bench(tmp_path, daemon_log)
    for number in range(70):
        b.door.hand_as_is({"id": f"flood-{number:03d}", "sealed": "A" * 120})
    await b.start()
    await b.door.polled(2)
    await b.stop()
    said = b.logged("door.unreachable")
    assert said and "not the contract's" in said[0]["reason"]
    assert not b.lines("flood-000") and not b.lines("flood-063")
    assert not b.door.refusals, "nothing of an answer that was not read is refused either"


async def test_what_the_door_refuses_is_said_and_the_daemon_asks_again(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)
    naps: list[float] = []

    async def nap(seconds: float) -> None:
        naps.append(seconds)
        await asyncio.sleep(0)

    b.door.refuse_next("poll", OutpostDoorCode.STALE_IAT)
    b.door.refuse_next("poll", OutpostDoorCode.POLL_CONFLICT, retry_after=1)
    b.door.refuse_next("poll", OutpostDoorCode.RATE_LIMITED, retry_after=17)
    b.door.refuse_next("poll", OutpostDoorCode.REQUEST_MISMATCH)
    call = b.door.submit("tracker", "search_issues", {"query": "x"})
    await b.start(sleep=nap, backoff=Backoff(rng=random.Random(5)))
    assert (await b.door.answered(call.id)).outcome == "result"
    await b.stop()
    # A clock the door refuses is said with what the door's own says: it is not signed, so the daemon
    # reports it and changes nothing on its word.
    (drift,) = b.logged("outpost.clock_drift")
    assert drift["level"] == "error" and "server_time" in drift and abs(drift["drift_s"]) < 5
    assert b.logged("door.poll_conflict")[0]["level"] == "warning"
    refused = b.logged("door.refused")
    assert [(line["code"], line["status"]) for line in refused] == [
        ("rate_limited", 429),
        ("request_mismatch", 401),
    ]
    # The door's word on when to come back is taken, and the wait still doubles.
    assert len(naps) == 4 and naps[2] >= 17 and naps[3] > naps[0]


async def test_a_426_a_proxy_stripped_of_its_problem_still_stops_the_daemon(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log)

    def stripped(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(426, text="Upgrade Required")

    await b.start(transport=httpx.MockTransport(stripped))
    assert await b.ended() is Exit.UPGRADE_REQUIRED
    said = b.logged("outpost.protocol_unsupported")
    assert said and said[0]["speaks"] == 1 and said[0]["door_minimum"] is None
