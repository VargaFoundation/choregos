"""The rendezvous, from the side of whoever routes a call to an outpost (S65-12, ADR 0149 §4-5).

A call through an outpost is a row with a short life: placed, claimed by one instance at its next
poll, answered or refused, then emptied. These tests use the primitives the broker's client will be
built on (S65-13): `place`, `wait`, `read`, `cancel`, `expire`, `forget` and `sweep`, with a scripted
daemon on the other side of the door. Delivery is the subject: a call is placed once under its id, a
call that was ever claimed is never claimed again, a claimed write whose lease ends is in doubt, and
only a call no instance claimed may be placed again.

They run on SQLite and, with `CHOREGOS_TEST_SUITE_ON_POSTGRES`, on PostgreSQL under row-level security.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from choregos_adapters import sealing
from choregos_contracts import OutpostCallState, outpost
from httpx import AsyncClient

from .kits import outpost_daemon as daemon_kit
from .kits import outposts as kit
from .kits.outpost_daemon import SLUG, Daemon, answer_path


@pytest.fixture(autouse=True)
def door(monkeypatch: pytest.MonkeyPatch) -> kit.Door:
    daemon_kit.key_wrapper(monkeypatch)
    return kit.switch_on(monkeypatch)


@pytest.fixture
async def daemon(client: AsyncClient, admin: str) -> Daemon:
    return await daemon_kit.approved(client)


@pytest.fixture
def quick_looks(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.outposts import rendezvous

    monkeypatch.setattr(rendezvous, "LOOK_S", (0.02, 0.05))


async def _outpost_id(slug: str = SLUG) -> str:
    return str((await kit.outpost_row(slug)).id)


def _session(scope: Any = None) -> Any:
    from choregos_api.db.session import session_scope

    return session_scope(orgs=scope or [kit.ORG])


async def _past(call_id: str, slug: str = SLUG, **moments: str) -> None:
    """Move a call's clocks into the past: what waiting would do."""
    from choregos_api.db.models import OutpostCall

    long_ago = datetime.now(UTC) - timedelta(seconds=1)
    await daemon_kit.change(OutpostCall, call_id, slug, **dict.fromkeys(moments or ("expires_at",), long_ago))


async def _calls(slug: str = SLUG) -> list[Any]:
    from choregos_api.db.models import OutpostCall

    return list(await kit.rows_of(slug, OutpostCall))


# ───────────────────────── placing ─────────────────────────


async def test_a_call_is_placed_as_a_row_that_waits_with_its_arguments_sealed(
    client: AsyncClient, daemon: Daemon
) -> None:
    from choregos_api.outposts import rendezvous
    from choregos_api.services import outposts as service

    connector = await kit.connector_at(SLUG)
    arguments = {"title": "Door stuck", "labels": ["bug"]}
    before = datetime.now(UTC)
    placed = await daemon_kit.place(
        call_id=rendezvous.call_id_of("5e1f0c7a-3b9d-4a62-8f14-d2c6b8a09e37", "0"),
        tool="create_issue",
        arguments=arguments,
        write=True,
        asserted={"sub": "action:5e1f0c7a", "org": kit.ORG, "not_a_name_of_the_contract": "dropped"},
        timeout_s=45,
        connector_id=connector,
        run_id="r-42",
        action_id="5e1f0c7a-3b9d-4a62-8f14-d2c6b8a09e37",
    )
    assert (placed.state, placed.created) == (OutpostCallState.PENDING, True)
    # A deterministic id, one path segment of the door: the same parts give the same call.
    assert placed.id == rendezvous.call_id_of("5e1f0c7a-3b9d-4a62-8f14-d2c6b8a09e37", "0")
    assert placed.id != rendezvous.call_id_of("5e1f0c7a-3b9d-4a62-8f14-d2c6b8a09e37", "1")
    (row,) = await _calls()
    assert (row.id, row.kind, row.state, row.endpoint, row.tool, row.write) == (
        placed.id,
        "call_tool",
        "pending",
        "tracker",
        "create_issue",
        True,
    )
    assert (row.connector_id, row.run_id, row.action_id) == (
        connector,
        "r-42",
        "5e1f0c7a-3b9d-4a62-8f14-d2c6b8a09e37",
    )
    assert row.asserted == {"sub": "action:5e1f0c7a", "org": kit.ORG}
    assert timedelta(seconds=44) < row.expires_at.replace(tzinfo=UTC) - before < timedelta(seconds=46)
    assert (row.instance_id, row.epoch, row.claimed_at, row.deadline, row.recorded) == (
        None,
        None,
        None,
        None,
        None,
    )
    # The arguments wait under the key wrapper, and so does the private half of the answer key.
    assert service.open_call_arguments(row) == arguments and row.arguments_sha256 == outpost.arguments_sha256(
        arguments
    )
    private = rendezvous._answer_private_key(row)
    assert private is not None
    assert sealing.open_sealed(private, row.id, sealing.seal(row.answer_key, row.id, {"for": "this call"}))
    raw = private.private_bytes_raw()
    assert raw not in bytes(row.answer_key_ciphertext) and len(row.answer_key) == 43
    # Bound to this call's row: moved to another, the envelope does not open.
    row.id = "another-call"
    with pytest.raises(Exception, match="does not open"):
        rendezvous._answer_private_key(row)


async def test_the_same_id_is_the_same_call_and_is_placed_once(client: AsyncClient, daemon: Daemon) -> None:
    """A write's id is derived from its effect's key: a retried effect meets the first row, whatever
    became of it, and creates no second delivery (ADR 0149 §5)."""
    from choregos_api.outposts import rendezvous

    call = {"call_id": rendezvous.call_id_of("an-action", "0"), "write": True, "tool": "create_issue"}
    first = await daemon_kit.place(**call)
    again = await daemon_kit.place(**call)
    assert (first.created, again.created, again.state) == (True, False, OutpostCallState.PENDING)
    (handed,) = await daemon.calls(client)
    retried = await daemon_kit.place(**call)
    assert (retried.created, retried.state) == (False, OutpostCallState.CLAIMED)
    assert await daemon.calls(client) == [], "a retry after the claim hands nothing over again"
    assert (await daemon.answer(client, handed)).status_code == 200
    assert (await daemon_kit.place(**call)).state is OutpostCallState.ANSWERED
    assert len(await _calls()) == 1
    # An id names one call: the same id with other arguments, another tool or another kind is refused.
    for other in (
        {"arguments": {"query": "else"}},
        {"tool": "delete_issue"},
        {"write": False},
        {"endpoint": "forge"},
    ):
        with pytest.raises(rendezvous.NotAdmitted) as refusal:
            await daemon_kit.place(**call | other)
        assert refusal.value.reason == rendezvous.ID_TAKEN
    # And an id is one outpost's: another outpost's session does not see the row, the table does.
    await kit.create(client, "cold-store")
    with pytest.raises(rendezvous.NotAdmitted) as refusal:
        await daemon_kit.place("cold-store", **call)
    assert refusal.value.reason == rendezvous.ID_TAKEN and await _calls("cold-store") == []


async def test_a_call_is_not_placed_at_an_outpost_that_takes_none(
    client: AsyncClient, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Outposts switched off, an outpost paused or revoked: nothing is written (ADR 0149 §7)."""
    from choregos_api.outposts import rendezvous

    assert (await client.post(f"{kit.BASE}/{SLUG}/pause")).status_code == 200
    with pytest.raises(rendezvous.NotAdmitted) as paused:
        await daemon_kit.place()
    assert (await client.post(f"{kit.BASE}/{SLUG}/resume")).status_code == 200
    kit.configure(monkeypatch, outposts="off")
    with pytest.raises(rendezvous.NotAdmitted) as off:
        await daemon_kit.place()
    kit.configure(monkeypatch, outposts="on")
    assert (await client.post(f"{kit.BASE}/{SLUG}/revoke")).status_code == 200
    with pytest.raises(rendezvous.NotAdmitted) as revoked:
        await daemon_kit.place()
    reasons = (paused.value.reason, off.value.reason, revoked.value.reason)
    assert reasons == (rendezvous.PAUSED, rendezvous.SWITCHED_OFF, rendezvous.REVOKED)
    assert await _calls() == []


@pytest.mark.parametrize(
    "call",
    [
        {"call_id": "an id with spaces"},
        {"call_id": "an-action:0"},
        {"endpoint": "http://tracker.internal:8080/mcp"},
        {"tool": None},
        {"arguments": None},
        {"kind": "list_tools", "tool": "search_issues"},
        {"kind": "list_tools", "write": True},
        {"kind": "run_shell"},
        {"timeout_s": 0},
        {"timeout_s": 301},
        {"asserted": {"sub": "x" * 257}},
    ],
)
async def test_a_call_that_is_not_shaped_as_its_kind_says_is_the_callers_fault(
    client: AsyncClient, daemon: Daemon, call: dict[str, Any]
) -> None:
    with pytest.raises(ValueError):
        await daemon_kit.place(**call)
    assert await _calls() == []


async def test_without_a_key_wrapper_no_call_is_written(
    client: AsyncClient, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.secret_store import ENV_KEKS, SecretStoreUnavailable

    monkeypatch.delenv(ENV_KEKS)
    with pytest.raises(SecretStoreUnavailable):
        await daemon_kit.place()
    with pytest.raises(SecretStoreUnavailable):
        await daemon_kit.place(kind="list_tools")
    assert await _calls() == []


async def test_calls_in_flight_are_bounded_per_outpost_and_per_organisation(
    client: AsyncClient, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where a call is admitted (ADR 0148 §10): one more than the quota is refused before anything is
    written, and a place comes back when a call is answered, refused or out of time."""
    from choregos_api.outposts import rendezvous

    other = await daemon_kit.approved(client, "cold-store")
    two, five = (
        rendezvous.Quota(per_outpost=2, per_organisation=5),
        rendezvous.Quota(per_outpost=9, per_organisation=3),
    )
    first = await daemon_kit.place(quota=two)
    await daemon_kit.place(quota=two)
    with pytest.raises(rendezvous.NotAdmitted) as refusal:
        await daemon_kit.place(quota=two)
    assert refusal.value.reason == rendezvous.OUTPOST_QUOTA and str(2) in str(refusal.value)
    # The other outpost has its own count, and the organisation's is the sum.
    await daemon_kit.place("cold-store", quota=five)
    with pytest.raises(rendezvous.NotAdmitted) as refusal:
        await daemon_kit.place("cold-store", quota=five)
    assert refusal.value.reason == rendezvous.ORGANISATION_QUOTA
    assert (len(await _calls()), len(await _calls("cold-store"))) == (2, 1)
    # A claimed call is still in flight; an answered one is not.
    calls = await daemon.calls(client)
    with pytest.raises(rendezvous.NotAdmitted):
        await daemon_kit.place(quota=two)
    assert (await daemon.answer(client, calls[0])).status_code == 200
    await daemon_kit.place(quota=two)
    # Nor is one that ran out of time, whoever closes it.
    await _past(first.id if calls[0]["id"] != first.id else calls[1]["id"], deadline="deadline")
    (theirs,) = await other.calls(client)
    assert (await other.refuse(client, theirs["id"])).status_code == 200
    await daemon_kit.place(quota=two)
    # Without a quota of its own, a placement is under the deployment's.
    kit.configure(
        monkeypatch,
        outposts_calls_in_flight_per_outpost="1",
        outposts_calls_in_flight_per_organisation="1",
    )
    with pytest.raises(rendezvous.NotAdmitted) as refusal:
        await daemon_kit.place("cold-store")
    assert refusal.value.reason == rendezvous.ORGANISATION_QUOTA


async def test_a_transaction_places_several_calls(client: AsyncClient, daemon: Daemon) -> None:
    """The organisation's placements are serialised until the transaction ends; a transaction that
    places a second call does not wait for itself."""
    from choregos_api.outposts import rendezvous

    row = await kit.outpost_row(SLUG)
    async with _session() as session:
        for index in range(3):
            placed = await asyncio.wait_for(
                rendezvous.place(
                    session,
                    row,
                    call_id=f"batch-{index}",
                    kind=rendezvous.OutpostCallKind.LIST_TOOLS,
                    endpoint="tracker",
                    timeout_s=30,
                ),
                timeout=5,
            )
            assert placed.created
    assert sorted(call["id"] for call in await daemon.calls(client)) == ["batch-0", "batch-1", "batch-2"]


# ───────────────────────── claiming ─────────────────────────


async def test_a_claimer_that_finds_a_call_claimed_since_it_read_it_hands_nothing_over(
    client: AsyncClient, daemon: Daemon
) -> None:
    """Of two claimers one wins, whichever database holds the row. The claim is a compare-and-set: a
    claimer that read a call as free hands it over only if the row is still unclaimed when it writes.
    PostgreSQL also locks what a claimer reads, so the second one skips it instead."""
    from choregos_api.db.models import OutpostInstance
    from choregos_api.outposts import rendezvous
    from sqlalchemy import select

    (other,) = await daemon_kit.instances(1)
    placed = await daemon_kit.place()
    outpost_id = await _outpost_id()
    now = datetime.now(UTC)

    async def instance_of(scope: Any, who: Daemon) -> Any:
        found = await scope.session.execute(
            select(OutpostInstance).where(OutpostInstance.id == who.instance_id)
        )
        return found.scalar_one()

    async with rendezvous.in_scope(outpost_id) as slow:
        (read,) = await rendezvous._unclaimed(slow.session, slow.outpost, ["tracker"], 4, now)
        assert read.id == placed.id
        async with rendezvous.in_scope(outpost_id) as quick:
            taken = await rendezvous.claim(
                quick.session,
                quick.outpost,
                await instance_of(quick, other),
                capacity=4,
                endpoints=["tracker"],
                capabilities=["tools"],
            )
        mine = await rendezvous._hand_over(
            slow.session, slow.outpost, await instance_of(slow, daemon), read, now
        )
    handed = [element["id"] for element in (*taken, *([mine] if mine else []))]
    assert handed == [placed.id], "handed over once, by one of the two"
    row = await daemon_kit.call_row(placed.id)
    winner = other if taken else daemon
    assert (row.state, row.instance_id) == ("claimed", winner.instance_id)
    assert winner.opened((taken or [mine])[0])["id"] == placed.id


async def test_a_call_is_claimed_only_while_the_outpost_is_active_and_the_instance_approved(
    client: AsyncClient, daemon: Daemon
) -> None:
    """The conditions of a claim, at the claim itself: whoever calls it."""
    from choregos_api.outposts import rendezvous

    placed = await daemon_kit.place()
    outpost_id = await _outpost_id()

    async def claimed(**changed: Any) -> list[str]:
        """A claim for the daemon's instance, the outpost or the instance being what `changed` says:
        in memory only, as a claimer that read them so would hold them."""
        async with rendezvous.in_scope(outpost_id) as scope:
            instance = await daemon_kit.instance_row(str(daemon.instance_id))
            scope.session.expunge(scope.outpost)
            now = changed.pop("now", None)
            for name, value in changed.items():
                target = instance if name.startswith("instance_") else scope.outpost
                setattr(target, name.removeprefix("instance_"), value)
            got = await rendezvous.claim(
                scope.session,
                scope.outpost,
                instance,
                capacity=4,
                endpoints=["tracker"],
                capabilities=["tools"],
                now=now,
            )
            return [element["id"] for element in got]

    assert await claimed(status="paused") == []
    assert await claimed(now=datetime.now(UTC) + timedelta(seconds=31)) == [], "its time is up"
    assert await claimed(status="revoked") == []
    assert await claimed(instance_status="pending") == []
    assert await claimed(instance_status="revoked") == []
    assert await claimed(capabilities=[]) == [], "the organisation enabled it for nothing"
    assert (await daemon_kit.call_row(placed.id)).state == "pending"
    assert await claimed() == [placed.id]


# ───────────────────────── leases that end ─────────────────────────


async def test_what_runs_out_of_time_is_closed_by_what_it_was(client: AsyncClient, daemon: Daemon) -> None:
    """An unclaimed call is `expired`: nothing reached any server. A claimed read is `timed_out`. A
    claimed write is `in_doubt`: nobody knows whether the server executed it (ADR 0149 §5)."""
    from choregos_api.outposts import rendezvous

    read, write = await daemon_kit.place(), await daemon_kit.place(write=True, tool="create_issue")
    answered = await daemon_kit.place()
    calls = {call["id"]: call for call in await daemon.calls(client)}
    assert (await daemon.answer(client, calls[answered.id])).status_code == 200
    unclaimed, in_time = await daemon_kit.place(), await daemon_kit.place()
    outpost_id = await _outpost_id()
    async with _session() as session:
        assert await rendezvous.expire(session, outpost_id) == 0, "nothing ran out of time yet"
    for call_id in (read.id, write.id, answered.id):
        await _past(call_id, deadline="deadline")
    await _past(unclaimed.id)
    async with _session() as session:
        assert await rendezvous.expire(session, outpost_id) == 3
        assert await rendezvous.expire(session, outpost_id) == 0, "each moves once"
    states = {row.id: row for row in await _calls()}
    assert {call_id: row.state for call_id, row in states.items()} == {
        read.id: "timed_out",
        write.id: "in_doubt",
        answered.id: "answered",  # what was recorded in time stays recorded
        unclaimed.id: "expired",
        in_time.id: "pending",
    }
    for call_id in (read.id, write.id, unclaimed.id):
        row = states[call_id]
        assert row.finished_at is not None and row.recorded is None
        assert (row.arguments_ciphertext, row.asserted, row.answer_key_ciphertext) == (None, None, None)
        assert row.arguments_sha256 is not None, "the digest stays"
    # The claim stays on the row: an in-doubt write is shown with who held it and since when.
    assert (states[write.id].instance_id, states[write.id].epoch) == (daemon.instance_id, 0)
    assert states[write.id].claimed_at is not None
    # Too late for all of them.
    daemon_kit.refused(await daemon.answer(client, calls[write.id]), "lease_lost", 409)
    daemon_kit.refused(await daemon.answer(client, calls[read.id]), "lease_lost", 409)
    # What was in time is still handed over; nothing that ran out is.
    assert [call["id"] for call in await daemon.calls(client)] == [in_time.id]


async def test_a_call_no_instance_claimed_is_placed_again_under_its_id_and_no_other_is(
    client: AsyncClient, daemon: Daemon
) -> None:
    """A write nobody claimed reached no server: it alone is retried. A read whose lease ended is asked
    again as a new call: the journal of the instance that held it would refuse a copy under its id."""
    from choregos_api.outposts import rendezvous

    write = {"call_id": "write-0", "write": True, "tool": "create_issue"}
    read = {"call_id": "read-0"}
    await daemon_kit.place(**write)
    await _past("write-0")
    again = await daemon_kit.place(**write, timeout_s=60)
    assert (again.created, again.state) == (False, OutpostCallState.PENDING), "placed again, the same row"
    row = await daemon_kit.call_row("write-0")
    assert (
        row.finished_at is None
        and row.arguments_ciphertext is not None
        and row.answer_key_ciphertext is not None
    )
    assert row.expires_at.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(seconds=50)
    (handed,) = await daemon.calls(client)
    assert handed["id"] == "write-0"

    # Claimed, then out of time: in doubt, and never placed nor claimed again.
    await _past("write-0", deadline="deadline", expires_at="expires_at")
    assert (await daemon_kit.place(**write)).state is OutpostCallState.IN_DOUBT
    assert (await daemon_kit.place(**write)).state is OutpostCallState.IN_DOUBT
    await daemon_kit.place(**read)
    (held,) = await daemon.calls(client)
    await _past("read-0", deadline="deadline", expires_at="expires_at")
    assert (await daemon_kit.place(**read)).state is OutpostCallState.TIMED_OUT
    assert await daemon.calls(client) == []
    assert held["id"] == "read-0" and len(await _calls()) == 2
    # The retry of the read is a new call.
    retried = await daemon_kit.place(call_id=rendezvous.new_call_id())
    assert [call["id"] for call in await daemon.calls(client)] == [retried.id]


async def test_a_cancel_closes_a_call_whatever_the_time(client: AsyncClient, daemon: Daemon) -> None:
    from choregos_api.outposts import rendezvous

    unclaimed, answered = await daemon_kit.place(), await daemon_kit.place(endpoint="forge")
    (call,) = await daemon.calls(client, endpoints=["forge"])
    assert (await daemon.answer(client, call)).status_code == 200
    outpost_id = await _outpost_id()
    async with _session() as session:
        assert await rendezvous.cancel(session, outpost_id, unclaimed.id) is OutpostCallState.EXPIRED
        assert await rendezvous.cancel(session, outpost_id, answered.id) is OutpostCallState.ANSWERED
        assert await rendezvous.cancel(session, outpost_id, "no-such-call") is None
    assert await daemon.calls(client) == [], "a cancelled call is handed to nobody"
    assert (await daemon_kit.call_row(answered.id)).answer_sealed is not None


# ───────────────────────── waiting, reading ─────────────────────────


async def test_waiting_returns_what_the_door_recorded(
    client: AsyncClient, daemon: Daemon, quick_looks: None
) -> None:
    from choregos_api.outposts import rendezvous

    placed = await daemon_kit.place()
    outpost_id = await _outpost_id()
    waiting = asyncio.create_task(rendezvous.wait(outpost_id, placed.id))
    await asyncio.sleep(0.2)
    assert not waiting.done(), "nothing is known while the call waits for a claim"
    (call,) = await daemon.calls(client)
    await asyncio.sleep(0.2)
    assert not waiting.done(), "nor while it waits for its answer"
    assert (await daemon.answer(client, call, result={"content": [], "isError": True})).status_code == 200
    outcome = await asyncio.wait_for(waiting, timeout=5)
    assert outcome is not None and not outcome.waiting
    assert outcome.state is OutpostCallState.ANSWERED and outcome.answer is not None
    assert outcome.answer.result == {"content": [], "isError": True}
    row = await daemon_kit.call_row(placed.id)
    assert (row.outcome, row.is_error) == ("result", True)
    assert await rendezvous.wait(outpost_id, "no-such-call") is None
    assert await rendezvous.wait("00000000-0000-0000-0000-000000000000", placed.id) is None


@pytest.mark.parametrize(
    ("write", "claimed", "state"),
    [(False, False, "expired"), (False, True, "timed_out"), (True, True, "in_doubt")],
)
async def test_waiting_ends_when_the_calls_time_is_up(
    client: AsyncClient, daemon: Daemon, quick_looks: None, write: bool, claimed: bool, state: str
) -> None:
    """A read fails fast when no instance claims it in time; a claimed write that gets no answer
    within its lease is in doubt. Whoever waits closes the call itself: nothing else has to run."""
    from choregos_api.outposts import rendezvous

    started = time.monotonic()
    placed = await daemon_kit.place(write=write, timeout_s=2)
    if claimed:
        assert len(await daemon.calls(client)) == 1
    outcome = await asyncio.wait_for(rendezvous.wait(await _outpost_id(), placed.id), timeout=15)
    assert outcome is not None and outcome.state.value == state and outcome.answer is None
    assert 1.9 < time.monotonic() - started < 8, "at the call's own time, neither sooner nor much later"
    assert (outcome.instance_id is not None) is claimed
    assert (await daemon_kit.call_row(placed.id)).state == state


@pytest.mark.parametrize(("write", "state"), [(True, "in_doubt"), (False, "timed_out")])
async def test_an_answer_that_says_unanswered_leaves_a_write_in_doubt(
    client: AsyncClient, daemon: Daemon, write: bool, state: str
) -> None:
    """The call was sent, or begun, and the outpost holds no answer: executed or not, nobody knows.
    The reader moves the state; what the door recorded does not move."""
    from choregos_api.outposts import rendezvous

    placed = await daemon_kit.place(write=write)
    (call,) = await daemon.calls(client)
    assert (await daemon.answer(client, call, outcome="unanswered")).status_code == 200
    assert (await daemon_kit.call_row(placed.id)).state == "answered", "the door does not open an answer"
    async with _session() as session:
        outcome = await rendezvous.read(session, await _outpost_id(), placed.id)
    assert outcome is not None and (outcome.state.value, outcome.answer) == (state, None)
    row = await daemon_kit.call_row(placed.id)
    assert (row.state, row.outcome, row.recorded, row.recorded_by) == (
        state,
        "unanswered",
        "answer",
        daemon.instance_id,
    )
    assert (await daemon.answer(client, call)).json() == {"id": placed.id, "recorded": "answer"}


async def test_an_answer_that_does_not_open_is_no_answer(client: AsyncClient, daemon: Daemon) -> None:
    """A hostile or broken outpost may store anything: an envelope sealed to another key, an answer to
    another call, something that is not an answer. Whoever reads it learns nothing, and says so."""
    from choregos_api.outposts import rendezvous

    placed = [await daemon_kit.place(write=index == 0) for index in range(3)]
    calls = {call["id"]: call for call in await daemon.calls(client)}
    stranger = sealing.new_key()[1]
    garbage = [
        sealing.seal(stranger, placed[0].id, {"id": placed[0].id, "outcome": "result", "result": {}}),
        sealing.seal(
            calls[placed[1].id]["answer_key"],
            placed[1].id,
            {"id": "another", "outcome": "result", "result": {}},
        ),
        sealing.seal(calls[placed[2].id]["answer_key"], placed[2].id, {"tools": "all of them"}),
    ]
    for call, sealed in zip(placed, garbage, strict=True):
        assert (await daemon.send(client, answer_path(call.id), {"sealed": sealed})).status_code == 200
    async with _session() as session:
        outcomes = [await rendezvous.read(session, await _outpost_id(), call.id) for call in placed]
    assert [(o.state.value, o.answer) for o in outcomes if o] == [
        ("in_doubt", None),
        ("timed_out", None),
        ("timed_out", None),
    ]


async def test_reading_before_anything_is_known_says_so(client: AsyncClient, daemon: Daemon) -> None:
    from choregos_api.outposts import rendezvous

    placed = await daemon_kit.place()
    outpost_id = await _outpost_id()
    async with _session() as session:
        pending = await rendezvous.read(session, outpost_id, placed.id)
        assert pending is not None and pending.waiting and pending.state is OutpostCallState.PENDING
        assert pending.until is not None and pending.until > datetime.now(UTC)
    await daemon.calls(client)
    async with _session(scope="*") as session:
        claimed = await rendezvous.read(session, outpost_id, placed.id)
        assert claimed is not None and claimed.waiting and claimed.instance_id == daemon.instance_id
        assert await rendezvous.read(session, outpost_id, "no-such-call") is None


# ───────────────────────── the platform's sweep ─────────────────────────


async def test_the_sweep_closes_what_ran_out_of_time_at_every_outpost(
    client: AsyncClient, daemon: Daemon
) -> None:
    """For the outposts nobody polls or calls any more. It runs as the platform, under whose scope no
    outpost's row exists until an outpost is named: it lists the registry and names each in turn."""
    from choregos_api.outposts import rendezvous

    other = await daemon_kit.approved(client, "cold-store")
    here, there = (
        await daemon_kit.place(write=True, tool="create_issue"),
        await daemon_kit.place("cold-store"),
    )
    unread = await daemon_kit.place()
    calls = {call["id"]: call for call in await daemon.calls(client)}
    await other.calls(client)
    assert (await daemon.answer(client, calls[unread.id])).status_code == 200
    assert await rendezvous.sweep() == 0
    await _past(here.id, deadline="deadline")
    await _past(there.id, "cold-store", deadline="deadline")
    assert await rendezvous.sweep() == 2
    assert (await daemon_kit.call_row(here.id)).state == "in_doubt"
    assert (await daemon_kit.call_row(there.id, "cold-store")).state == "timed_out"
    # An answer nobody came to read does not stay sealed on its row for ever.
    assert (await daemon_kit.call_row(unread.id)).answer_sealed is not None
    await _past(unread.id, finished_at="finished_at")
    await rendezvous.sweep(now=datetime.now(UTC) + timedelta(seconds=rendezvous.ANSWER_KEPT_S - 5))
    assert (await daemon_kit.call_row(unread.id)).answer_sealed is not None
    await rendezvous.sweep(now=datetime.now(UTC) + timedelta(seconds=rendezvous.ANSWER_KEPT_S + 5))
    kept = await daemon_kit.call_row(unread.id)
    assert (kept.answer_sealed, kept.answer_key_ciphertext, kept.state, kept.recorded) == (
        None,
        None,
        "answered",
        "answer",
    )


async def test_the_api_sweeps_while_it_runs_when_outposts_are_on(
    app: Any, client: AsyncClient, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The sweep is started with the application, and only where outposts are switched on."""
    from choregos_api.main import create_app

    placed = await daemon_kit.place(write=True, tool="create_issue")
    await daemon.calls(client)
    await _past(placed.id, deadline="deadline")
    kit.configure(monkeypatch, outposts="off")
    switched_off = create_app()
    async with switched_off.router.lifespan_context(switched_off):
        await asyncio.sleep(0.5)
    kit.configure(monkeypatch, outposts="on")
    assert (await daemon_kit.call_row(placed.id)).state == "claimed", "off, nothing sweeps"
    async with app.router.lifespan_context(app):
        for _ in range(100):
            if (await daemon_kit.call_row(placed.id)).state != "claimed":
                break
            await asyncio.sleep(0.05)
    assert (await daemon_kit.call_row(placed.id)).state == "in_doubt"
