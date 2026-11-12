"""The rendezvous on a real PostgreSQL: what two replicas, two instances and two transactions do to
one row (S65-12, ADR 0148 §4 and §6-7, ADR 0149 §5).

SQLite has no row lock, no row-level security and no `NOTIFY`: what is proven here cannot be proven
there. Of two claimers one wins, and the other does not wait for it. A call that was ever claimed is
never claimed again, a write above all. An answer under a previous epoch is refused, even when the
revocation commits while the door is looking. A sequence, a poll slot and a quota are each taken once
by requests that arrive together. The door's session sees one outpost's rows. A wake-up leaves with
the commit of the row it announces, never before.

Runs only with `CHOREGOS_TEST_DATABASE_URL`: the schema is the migration's, the role is not a
superuser, and no session has a scope it did not ask for (`pg_app`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from choregos_contracts import OutpostCallState
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from .conftest import PG_URL, login, without_postgres
from .kits import outpost_daemon as daemon_kit
from .kits import outposts as kit
from .kits.outpost_daemon import POLL, SLUG, Daemon, compact, refused

pytestmark = without_postgres


@pytest.fixture(autouse=True)
def door(monkeypatch: pytest.MonkeyPatch) -> kit.Door:
    daemon_kit.key_wrapper(monkeypatch)
    return kit.switch_on(monkeypatch)


@pytest.fixture
async def client(pg_app: Any, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncClient]:
    """The application on the migrated schema, an organisation and its administrator signed in."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    monkeypatch.setenv("CHOREGOS_OIDC_DEFAULT_ORG", kit.ORG)
    monkeypatch.setenv("CHOREGOS_DEV_ADMIN_EMAILS", "admin@varga.dev")
    reset_settings_cache()
    async with session_scope(orgs="*") as session:
        session.add(Organization(slug=kit.ORG, name="Varga Foundation"))
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as http:
        await login(http, "admin@varga.dev")
        yield http


@pytest.fixture
async def daemon(client: AsyncClient) -> Daemon:
    return await daemon_kit.approved(client)


async def _scope(outpost_id: str) -> Any:
    from choregos_api.outposts import rendezvous

    return rendezvous.in_scope(outpost_id)


async def _instance(session: Any, daemon: Daemon) -> Any:
    from choregos_api.db.models import OutpostInstance

    return (
        await session.execute(select(OutpostInstance).where(OutpostInstance.id == daemon.instance_id))
    ).scalar_one()


def _claim(scope: Any, instance: Any, capacity: int = 4) -> Any:
    from choregos_api.outposts import rendezvous

    return rendezvous.claim(
        scope.session,
        scope.outpost,
        instance,
        capacity=capacity,
        endpoints=["tracker"],
        capabilities=["tools"],
    )


# ───────────────────────── of two claimers one wins ─────────────────────────


async def test_of_two_claimers_one_wins(client: AsyncClient, daemon: Daemon) -> None:
    """Two instances of an outpost poll at the same moment, at two replicas for all the door knows:
    every call is handed to one of them, and to one only."""
    (other,) = await daemon_kit.instances(1)
    placed = sorted([(await daemon_kit.place()).id for _ in range(12)])
    taken: dict[str, list[str]] = {str(daemon.instance_id): [], str(other.instance_id): []}
    for _ in range(4):
        first, second = await asyncio.gather(daemon.poll(client, capacity=3), other.poll(client, capacity=3))
        assert (first.status_code, second.status_code) == (200, 200)
        taken[str(daemon.instance_id)] += [daemon.opened(call)["id"] for call in first.json()["calls"]]
        taken[str(other.instance_id)] += [other.opened(call)["id"] for call in second.json()["calls"]]
    handed = [call_id for calls in taken.values() for call_id in calls]
    assert sorted(handed) == placed, "each call once, none twice, none left"
    for instance_id, calls in taken.items():
        for call_id in calls:
            assert (await daemon_kit.call_row(call_id)).instance_id == instance_id


async def test_a_claimer_does_not_wait_for_a_row_another_is_claiming(
    client: AsyncClient, daemon: Daemon
) -> None:
    """`FOR UPDATE SKIP LOCKED`: while one transaction holds a call it is claiming, another claimer
    gets the calls that are free and returns at once; it never sits behind the first."""
    from choregos_api.outposts import rendezvous

    (other,) = await daemon_kit.instances(1)
    placed = [(await daemon_kit.place()).id for _ in range(2)]
    outpost_id = (await kit.outpost_row(SLUG)).id
    async with rendezvous.in_scope(outpost_id) as slow:
        assert slow is not None
        held = await _claim(slow, await _instance(slow.session, daemon), capacity=1)
        assert [call["id"] for call in held] == placed[:1]
        # Not committed yet: the other claimer must neither wait for the row nor take it.
        started = time.monotonic()
        async with rendezvous.in_scope(outpost_id) as quick:
            assert quick is not None
            got = await asyncio.wait_for(_claim(quick, await _instance(quick.session, other)), timeout=5)
        assert time.monotonic() - started < 2
        assert [call["id"] for call in got] == placed[1:]
    rows = {call_id: (await daemon_kit.call_row(call_id)).instance_id for call_id in placed}
    assert rows == {placed[0]: daemon.instance_id, placed[1]: other.instance_id}


# ───────────────────────── never claimed again ─────────────────────────


async def test_a_claimed_write_is_never_claimed_again(client: AsyncClient, daemon: Daemon) -> None:
    """One claim. Not after a timeout, not when its instance disappears, not when its effect is
    retried, not by another instance, and not even if its row were put back to `pending` (ADR 0149 §5)."""
    from choregos_api.db.models import OutpostCall
    from choregos_api.outposts import rendezvous

    (other,) = await daemon_kit.instances(1)
    write = {"call_id": rendezvous.call_id_of("an-action", "0"), "write": True, "tool": "create_issue"}
    placed = await daemon_kit.place(**write)
    (held,) = await daemon.calls(client)
    assert held["id"] == placed.id
    for poller in (daemon, other):
        assert await poller.calls(client) == []
    # Its effect is retried while the instance holds it: the first row answers, nothing is placed.
    assert (await daemon_kit.place(**write)).state is OutpostCallState.CLAIMED
    # A hand, or a fault, that put the row back to `pending` does not get it handed over a second time.
    await daemon_kit.change(OutpostCall, placed.id, state="pending")
    for poller in (daemon, other):
        assert await poller.calls(client) == [], "a row that was ever claimed is not claimed again"
    await daemon_kit.change(OutpostCall, placed.id, state="claimed")
    # Its lease ends: in doubt, for good.
    long_ago = datetime.now(UTC) - timedelta(seconds=1)
    await daemon_kit.change(OutpostCall, placed.id, deadline=long_ago, expires_at=long_ago)
    assert (await daemon_kit.place(**write, timeout_s=60)).state is OutpostCallState.IN_DOUBT
    for poller in (daemon, other):
        assert await poller.calls(client) == []
    row = await daemon_kit.call_row(placed.id)
    assert (row.state, row.instance_id, row.recorded) == ("in_doubt", daemon.instance_id, None)
    refused(await daemon.answer(client, held), "lease_lost", 409)
    # Its instance is revoked: still not another's.
    assert (await client.post(f"{kit.BASE}/{SLUG}/instances/{daemon.instance_id}/revoke")).status_code == 200
    assert await other.calls(client) == []
    assert (await daemon_kit.place(**write)).state is OutpostCallState.IN_DOUBT


async def test_a_read_whose_lease_ended_is_not_handed_over_again_under_its_id(
    client: AsyncClient, daemon: Daemon
) -> None:
    """The journal of the instance that held it decides first, and would refuse a copy whose deadline is
    behind. The retry of a read is a new call, which any instance may claim."""
    from choregos_api.db.models import OutpostCall
    from choregos_api.outposts import rendezvous

    (other,) = await daemon_kit.instances(1)
    placed = await daemon_kit.place(call_id="a-read")
    assert len(await daemon.calls(client)) == 1
    long_ago = datetime.now(UTC) - timedelta(seconds=1)
    await daemon_kit.change(OutpostCall, placed.id, deadline=long_ago, expires_at=long_ago)
    assert (await daemon_kit.place(call_id="a-read")).state is OutpostCallState.TIMED_OUT
    for poller in (daemon, other):
        assert await poller.calls(client) == []
    retried = await daemon_kit.place(call_id=rendezvous.new_call_id())
    assert [call["id"] for call in await daemon.calls(client)] == [retried.id]


# ───────────────────────── the epoch ─────────────────────────


async def test_an_answer_under_a_previous_epoch_is_refused(client: AsyncClient, daemon: Daemon) -> None:
    """A revocation moves the outpost's epoch (ADR 0147 §5), and an answer is taken only under the epoch
    its call was claimed in. The door reads the epoch in the statement that records the answer: a
    revocation that commits while the door is looking is seen."""
    from choregos_api.outposts import rendezvous

    (other,) = await daemon_kit.instances(1)
    read, write = await daemon_kit.place(), await daemon_kit.place(write=True, tool="create_issue")
    calls = {call["id"]: call for call in await daemon.calls(client)}
    outpost_id = (await kit.outpost_row(SLUG)).id

    # The door has read the outpost, under epoch 0, and has not recorded yet.
    async with rendezvous.in_scope(outpost_id) as looking:
        assert looking is not None and looking.outpost.epoch == 0
        instance = await _instance(looking.session, daemon)
        revoked = await client.post(f"{kit.BASE}/{SLUG}/instances/{other.instance_id}/revoke")
        assert revoked.status_code == 200 and (await kit.outpost_row(SLUG)).epoch == 1
        with pytest.raises(rendezvous.LeaseLost):
            await rendezvous.record(looking.session, looking.outpost, instance, read.id, answer="A" * 80)
    assert (await daemon_kit.call_row(read.id)).recorded is None

    for call_id in (read.id, write.id):
        refused(await daemon.answer(client, calls[call_id]), "lease_lost", 409)
        refused(await daemon.refuse(client, call_id), "lease_lost", 409)
        row = await daemon_kit.call_row(call_id)
        assert (row.recorded, row.epoch, row.state) == (None, 0, "claimed")
    # What is claimed from now on is claimed, and answered, under the new epoch.
    fresh = await daemon_kit.place()
    (again,) = await daemon.calls(client)
    assert (await daemon.answer(client, again)).status_code == 200
    row = await daemon_kit.call_row(fresh.id)
    assert (row.epoch, row.recorded_epoch, row.recorded) == (1, 1, "answer")


async def test_an_answer_that_arrives_twice_at_once_is_recorded_once_and_read_back_by_the_other(
    client: AsyncClient, daemon: Daemon
) -> None:
    """A daemon retries: two copies of an answer may be in two transactions at the same moment. One
    records; the other waits for the row, finds the record made, and reads it back."""
    from choregos_api.outposts import rendezvous
    from choregos_contracts import OutpostRefusalCode

    placed = await daemon_kit.place()
    (call,) = await daemon.calls(client)
    sealed = daemon.sealed_answer(call)
    outpost_id = (await kit.outpost_row(SLUG)).id

    async def the_other_copy() -> str:
        async with rendezvous.in_scope(outpost_id) as scope:
            assert scope is not None
            instance = await _instance(scope.session, daemon)
            return await rendezvous.record(
                scope.session, scope.outpost, instance, placed.id, refusal=OutpostRefusalCode.BUSY
            )

    async with rendezvous.in_scope(outpost_id) as first:
        assert first is not None
        instance = await _instance(first.session, daemon)
        assert (
            await rendezvous.record(first.session, first.outpost, instance, placed.id, answer=sealed)
            == "answer"
        )
        other = asyncio.create_task(the_other_copy())
        await asyncio.sleep(0.7)
        assert not other.done(), "it waits for the row the first copy is writing"
    assert await asyncio.wait_for(other, timeout=10) == "answer", "the first of the two wins, for both"
    row = await daemon_kit.call_row(placed.id)
    assert (row.recorded, row.answer_sealed, row.refusal) == ("answer", sealed, None)


# ───────────────────────── taken once, by requests that arrive together ─────────────────────────


async def test_a_request_that_arrives_twice_at_once_is_taken_once(
    client: AsyncClient, daemon: Daemon
) -> None:
    """The sequence is a compare-and-set on the instance's row: at whichever replica each copy
    arrives, one of them takes it and the others are replays."""
    body = compact(daemon.poll_body())
    signed = daemon.sign(POLL, body)
    copies = await asyncio.gather(*(daemon.send(client, POLL, body, signature=signed) for _ in range(8)))
    statuses = sorted(response.status_code for response in copies)
    assert statuses == [200] + [401] * 7, statuses
    for response in copies:
        if response.status_code == 401:
            refused(response, "replayed_sequence", 401)


async def test_two_polls_that_arrive_together_do_not_both_take_the_slot(
    client: AsyncClient, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The slot is a compare-and-set on the instance's row: a poll that arrives while another is
    taking it waits for the row, then finds it taken. At two replicas as at one."""
    from choregos_api.outposts import auth, rendezvous
    from choregos_api.outposts import door as served
    from choregos_api.rbac import outpost_principal
    from choregos_api.services.outposts import POLL_CONFLICT
    from choregos_contracts import OutpostPollRequest

    outpost_id = (await kit.outpost_row(SLUG)).id
    body = OutpostPollRequest.model_validate(daemon.poll_body(wait_s=25))
    now = datetime.now(UTC)

    async def caller_in(scope: Any) -> Any:
        return auth.Caller(
            outpost=scope.outpost,
            organisation=scope.organisation,
            instance=await _instance(scope.session, daemon),
            principal=outpost_principal(outpost_id=outpost_id, instance_id=str(daemon.instance_id)),
        )

    async def the_other_poll() -> bool:
        async with rendezvous.in_scope(outpost_id) as scope:
            assert scope is not None
            return await served._take_the_slot(scope.session, await caller_in(scope), body, "the-other", now)

    async with rendezvous.in_scope(outpost_id) as first:
        assert first is not None
        assert await served._take_the_slot(first.session, await caller_in(first), body, "the-first", now)
        other = asyncio.create_task(the_other_poll())
        await asyncio.sleep(0.7)
        assert not other.done(), "it waits for the row the first poll is taking"
    assert await asyncio.wait_for(other, timeout=10) is False
    assert (await daemon_kit.instance_row(str(daemon.instance_id))).poll_holder == "the-first"
    from choregos_api.db.models import OutpostInstance

    await daemon_kit.change(OutpostInstance, str(daemon.instance_id), poll_holder=None, poll_expires_at=None)

    # Through the door: of polls sent together one waits, and none of the others does. A daemon's
    # requests sent at once may arrive out of order, so a poll that lost may also be a replay.
    monkeypatch.setattr(rendezvous, "LOOK_S", (0.05, 0.1))
    answers = await asyncio.gather(*(daemon.poll(client, wait_s=2) for _ in range(4)))
    statuses = sorted(response.status_code for response in answers)
    assert statuses.count(200) == 1 and set(statuses) <= {200, 401, 409}, statuses
    for response in answers:
        if response.status_code != 200:
            code = {401: "replayed_sequence", 409: "poll_conflict"}[response.status_code]
            refused(response, code, response.status_code)
    assert len(await kit.audit_rows(POLL_CONFLICT)) == min(1, statuses.count(409)), "one alert at most"
    daemon.nonce = next(r.headers["outpost-nonce"] for r in answers if r.status_code == 200)
    assert (await daemon.poll(client)).status_code == 200


async def test_two_placements_at_once_do_not_both_take_the_last_place(
    client: AsyncClient, daemon: Daemon
) -> None:
    """The count of calls in flight and the row that takes a place are one step per organisation: a
    placement that arrives while another has counted and not committed yet waits for it, then counts
    its row. Without that, both would count the same calls and both take the last place."""
    from choregos_api.db.session import session_scope
    from choregos_api.outposts import rendezvous

    quota = rendezvous.Quota(per_outpost=3, per_organisation=3)
    await daemon_kit.place(quota=quota)
    await daemon_kit.place(quota=quota)
    row = await kit.outpost_row(SLUG)
    async with session_scope(orgs=[kit.ORG]) as first:
        placed = await rendezvous.place(
            first,
            row,
            call_id="the-last-place",
            kind=rendezvous.OutpostCallKind.LIST_TOOLS,
            endpoint="tracker",
            timeout_s=30,
            quota=quota,
        )
        assert placed.created
        # Written, not committed: another placement must not count two calls and pass.
        second = asyncio.create_task(daemon_kit.place(quota=quota))
        await asyncio.sleep(0.7)
        assert not second.done(), "the second placement waits for the first to commit"
    with pytest.raises(rendezvous.NotAdmitted) as refusal:
        await asyncio.wait_for(second, timeout=10)
    assert refusal.value.reason == rendezvous.OUTPOST_QUOTA
    # Another organisation's placements do not wait behind this one's.
    assert rendezvous._lock_scope(row.org_id) != rendezvous._lock_scope("another-organisation")


# ───────────────────────── one outpost, never all of them ─────────────────────────


async def test_the_doors_session_sees_one_outposts_calls_and_answers_404_for_any_other(
    client: AsyncClient, daemon: Daemon
) -> None:
    """Whatever is not this outpost's does not exist for the door: of the same organisation or of
    another, from a query with no `WHERE` clause (ADR 0148 §6)."""
    from choregos_api.db.models import Organization, Outpost, OutpostCall
    from choregos_api.db.session import name_the_outpost, session_scope
    from choregos_api.outposts import rendezvous

    neighbour = await daemon_kit.approved(client, "cold-store")
    theirs = await daemon_kit.place("cold-store")
    mine = await daemon_kit.place()
    async with session_scope(orgs="*") as session:
        elsewhere = Organization(slug="elsewhere", name="Elsewhere")
        session.add(elsewhere)
        await session.flush()
        far = Outpost(org_id=elsewhere.id, slug=SLUG, display_name=SLUG, capabilities=["tools"])
        session.add(far)
        await session.flush()
        await name_the_outpost(session, far.id)
        foreign = OutpostCall(
            id="a-call-of-another-organisation",
            org_id=elsewhere.id,
            outpost_id=far.id,
            kind="call_tool",
            endpoint="tracker",
            tool="search_issues",
            write=False,
            state="claimed",
            epoch=0,
            deadline=datetime.now(UTC) + timedelta(seconds=60),
            claimed_at=datetime.now(UTC),
        )
        session.add(foreign)
        await session.flush()
        far_id = far.id

    outpost_id = (await kit.outpost_row(SLUG)).id
    async with rendezvous.in_scope(outpost_id) as scope:
        assert scope is not None
        seen = {str(row[0]) for row in await scope.session.execute(text("SELECT id FROM outpost_calls"))}
        assert seen == {mine.id}
        orgs, named = (
            await scope.session.execute(
                text(
                    "SELECT current_setting('app.current_orgs', true), "
                    "current_setting('app.current_outpost', true)"
                )
            )
        ).one()
        assert (orgs, named) == (kit.ORG, outpost_id), "one organisation, one outpost, never `*`"
        instance = await _instance(scope.session, daemon)
        for call_id in (theirs.id, foreign.id):
            with pytest.raises(rendezvous.NoSuchCall):
                await rendezvous.record(scope.session, scope.outpost, instance, call_id, answer="A" * 80)
        # And a claim with no outpost in its conditions would still take this outpost's rows only.
        claimed = await scope.session.execute(text("UPDATE outpost_calls SET state = 'claimed' RETURNING id"))
        assert {str(row[0]) for row in claimed} == {mine.id}
        await scope.session.rollback()
    async with session_scope(orgs="*") as session:
        assert await rendezvous.read(session, far_id, foreign.id) is not None, "its own outpost reads it"
        assert await rendezvous.read(session, outpost_id, foreign.id) is None
    for call_id in (theirs.id, foreign.id):
        refused(await daemon.refuse(client, call_id), "not_found", 404)
        refused(
            await daemon.send(client, daemon_kit.answer_path(call_id), {"sealed": "A" * 80}), "not_found", 404
        )
    (own,) = await daemon.calls(client)
    (call,) = await neighbour.calls(client)
    assert (own["id"], call["id"]) == (mine.id, theirs.id)


async def test_the_platforms_scope_sees_no_call_until_it_names_an_outpost(
    client: AsyncClient, daemon: Daemon
) -> None:
    """Which is why the sweep lists the registry and names each outpost in turn."""
    from choregos_api.db.models import OutpostCall
    from choregos_api.db.session import session_scope
    from choregos_api.outposts import rendezvous

    placed = await daemon_kit.place(write=True, tool="create_issue")
    await daemon.calls(client)
    long_ago = datetime.now(UTC) - timedelta(seconds=1)
    await daemon_kit.change(OutpostCall, placed.id, deadline=long_ago)
    async with session_scope(orgs="*") as session:
        assert (await session.execute(text("SELECT count(*) FROM outpost_calls"))).scalar_one() == 0
        moved = await session.execute(text("UPDATE outpost_calls SET state = 'in_doubt'"))
        assert moved.rowcount == 0  # type: ignore[attr-defined]
    assert (await daemon_kit.call_row(placed.id)).state == "claimed"
    assert await rendezvous.sweep() == 1
    assert (await daemon_kit.call_row(placed.id)).state == "in_doubt"


# ───────────────────────── the wake-up leaves with the commit ─────────────────────────


async def test_a_wake_up_leaves_with_the_commit_of_the_row_it_announces(
    client: AsyncClient, daemon: Daemon
) -> None:
    """Ids only, on a channel of its own, and never before the commit (ADR 0085): a poll woken earlier
    would look, find nothing, and go back to wait for the periodic look."""
    import asyncpg
    from choregos_api.db.session import session_scope
    from choregos_api.outposts import rendezvous

    heard: list[str] = []
    listener = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    await listener.add_listener(rendezvous.CHANNEL, lambda _c, _p, _channel, payload: heard.append(payload))
    row = await kit.outpost_row(SLUG)
    try:
        async with session_scope(orgs=[kit.ORG]) as session:
            placed = await rendezvous.place(
                session,
                row,
                call_id="announced",
                kind=rendezvous.OutpostCallKind.LIST_TOOLS,
                endpoint="tracker",
                timeout_s=30,
            )
            await asyncio.sleep(0.3)
            assert heard == [], "nothing leaves before the commit"
        await _until(lambda: heard == [row.id])
        (call,) = await daemon.calls(client)
        assert (await daemon.answer(client, call, tools=[])).status_code == 200
        await _until(lambda: heard == [row.id, f"{row.id}/{placed.id}"])
        # A placement that is rolled back announces nothing.
        with pytest.raises(RuntimeError, match="given up"):
            async with session_scope(orgs=[kit.ORG]) as session:
                await rendezvous.place(
                    session,
                    row,
                    call_id="never",
                    kind=rendezvous.OutpostCallKind.LIST_TOOLS,
                    endpoint="tracker",
                    timeout_s=30,
                )
                raise RuntimeError("given up")
        await asyncio.sleep(0.3)
        assert len(heard) == 2
    finally:
        await listener.close()


async def _until(condition: Any, timeout_s: float = 5.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "it never happened"
        await asyncio.sleep(0.02)
