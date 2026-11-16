# SPDX-License-Identifier: Apache-2.0
"""Calls per minute are capped where a call is admitted (S65-13, ADR 0148 §10).

Beside the calls in flight, the rendezvous bounds how many calls an outpost, and all the outposts of
an organisation, are handed in a minute. The count is read from the calls' own rows, in the
transaction that places the next one and under the lock that serialises an organisation's
placements: every replica reads the same number, and no replica's memory is asked. A call counts
from the moment it is placed, for sixty seconds, whatever becomes of it; a call that is refused was
never written and counts for nothing.

They run on SQLite and, with `CHOREGOS_TEST_SUITE_ON_POSTGRES`, on PostgreSQL under row-level
security. What two placements do at once is in `test_broker_via_outpost_postgres.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from .kits import outpost_daemon as daemon_kit
from .kits import outposts as kit
from .kits.outpost_daemon import SLUG, Daemon

T0 = datetime(2026, 11, 12, 9, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def door(monkeypatch: pytest.MonkeyPatch) -> kit.Door:
    daemon_kit.key_wrapper(monkeypatch)
    return kit.switch_on(monkeypatch)


@pytest.fixture
async def daemon(client: AsyncClient, admin: str) -> Daemon:
    return await daemon_kit.approved(client)


def _at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def _quota(**per_minute: int) -> Any:
    from choregos_api.outposts import rendezvous

    return rendezvous.Quota(per_outpost=100, per_organisation=100, **per_minute)


async def _calls(slug: str = SLUG) -> list[Any]:
    from choregos_api.db.models import OutpostCall

    return list(await kit.rows_of(slug, OutpostCall))


async def test_an_outpost_is_handed_no_more_calls_in_a_minute_than_its_cap(
    client: AsyncClient, daemon: Daemon
) -> None:
    from choregos_api.db.models import OutpostCall
    from choregos_api.outposts import rendezvous

    two = _quota(per_minute_per_outpost=2)
    first = await daemon_kit.place(quota=two, now=_at(0))
    await daemon_kit.place(quota=two, now=_at(30))
    with pytest.raises(rendezvous.NotAdmitted) as refusal:
        await daemon_kit.place(quota=two, now=_at(59))
    assert refusal.value.reason == rendezvous.OUTPOST_RATE
    assert SLUG in str(refusal.value) and "2 calls in the last minute" in str(refusal.value)
    assert len(await _calls()) == 2, "a call that is not admitted is not written"
    # A call counts for sixty seconds from the moment it was placed, whatever became of it: answered,
    # refused or out of time, it was handed to the outpost in that minute.
    await daemon_kit.change(OutpostCall, first.id, state="answered", finished_at=_at(1))
    with pytest.raises(rendezvous.NotAdmitted):
        await daemon_kit.place(quota=two, now=_at(59))
    # The minute is the last sixty seconds, not a slot of the clock: the first call left it.
    await daemon_kit.place(quota=two, now=_at(61))
    with pytest.raises(rendezvous.NotAdmitted):
        await daemon_kit.place(quota=two, now=_at(62))
    # The refusals above wrote nothing, so they did not count either.
    assert len(await _calls()) == 3
    await daemon_kit.place(quota=two, now=_at(91))


async def test_the_organisations_cap_counts_all_its_outposts_and_no_other_organisations(
    client: AsyncClient, daemon: Daemon
) -> None:
    from choregos_api.db.models import Organization, Outpost
    from choregos_api.db.session import session_scope
    from choregos_api.outposts import rendezvous

    await daemon_kit.approved(client, "cold-store")
    three = _quota(per_minute_per_outpost=2, per_minute_per_organisation=3)
    await daemon_kit.place(quota=three, now=_at(0))
    await daemon_kit.place(quota=three, now=_at(1))
    await daemon_kit.place("cold-store", quota=three, now=_at(2))
    # The outpost's own cap is said first; the other outpost meets the organisation's.
    with pytest.raises(rendezvous.NotAdmitted) as refusal:
        await daemon_kit.place(quota=three, now=_at(3))
    assert refusal.value.reason == rendezvous.OUTPOST_RATE
    with pytest.raises(rendezvous.NotAdmitted) as refusal:
        await daemon_kit.place("cold-store", quota=three, now=_at(3))
    assert refusal.value.reason == rendezvous.ORGANISATION_RATE
    assert "3 calls in the last minute" in str(refusal.value)
    # Another organisation's outposts count for themselves.
    async with session_scope(orgs="*") as session:
        other = Organization(slug="acme", name="Acme")
        session.add(other)
        await session.flush()
        theirs = Outpost(org_id=other.id, slug="depot", display_name="depot", capabilities=["tools"])
        session.add(theirs)
        await session.flush()
        placed = await rendezvous.place(
            session,
            theirs,
            call_id=rendezvous.new_call_id(),
            kind=rendezvous.OutpostCallKind.LIST_TOOLS,
            endpoint="tracker",
            timeout_s=30,
            quota=three,
            now=_at(3),
        )
        assert placed.created


async def test_a_call_placed_again_under_its_id_counts_in_the_minute_it_is_placed_again(
    client: AsyncClient, daemon: Daemon
) -> None:
    """Only a call no instance ever claimed is placed again under its id (ADR 0149 §5). It is handed
    to the outpost a second time, so it counts a second time, in its new minute."""
    from choregos_api.db.models import OutpostCall
    from choregos_api.outposts import rendezvous

    one = _quota(per_minute_per_outpost=1)
    first = await daemon_kit.place(call_id="a-write-nobody-claimed", quota=one, now=_at(0), write=True)
    assert first.created
    await daemon_kit.change(OutpostCall, first.id, state="expired", finished_at=_at(30))
    again = await daemon_kit.place(call_id=first.id, quota=one, now=_at(90), write=True)
    assert not again.created and again.state.value == "pending", "the same row, placed again"
    (row,) = await _calls()
    assert row.created_at.replace(tzinfo=UTC) == _at(90), "placed now: what the minute counts"
    with pytest.raises(rendezvous.NotAdmitted) as refusal:
        await daemon_kit.place(quota=one, now=_at(100))
    assert refusal.value.reason == rendezvous.OUTPOST_RATE


async def test_calls_in_flight_are_said_before_calls_per_minute_and_a_cap_may_be_lifted(
    client: AsyncClient, daemon: Daemon
) -> None:
    from choregos_api.outposts import rendezvous

    both = rendezvous.Quota(per_outpost=1, per_organisation=9, per_minute_per_outpost=1)
    await daemon_kit.place(quota=both, now=_at(0))
    with pytest.raises(rendezvous.NotAdmitted) as refusal:
        await daemon_kit.place(quota=both, now=_at(1))
    assert refusal.value.reason == rendezvous.OUTPOST_QUOTA
    # `None` lifts a cap per minute: what a quota that names none had before, and still has.
    lifted = rendezvous.Quota(per_outpost=100, per_organisation=100)
    assert not lifted.per_minute
    for second in range(5):
        await daemon_kit.place(quota=lifted, now=_at(second))


async def test_a_placement_without_a_quota_of_its_own_is_under_the_deployments(
    client: AsyncClient, daemon: Daemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import get_settings
    from choregos_api.outposts import rendezvous

    settings = get_settings()
    assert (
        settings.outposts_calls_per_minute_per_outpost,
        settings.outposts_calls_per_minute_per_organisation,
    ) == (
        600,
        2400,
    )
    kit.configure(
        monkeypatch,
        outposts_calls_per_minute_per_outpost="1",
        outposts_calls_per_minute_per_organisation="5",
    )
    assert rendezvous.Quota.of_the_deployment().per_minute_per_outpost == 1
    await daemon_kit.place()
    with pytest.raises(rendezvous.NotAdmitted) as refusal:
        await daemon_kit.place()
    assert refusal.value.reason == rendezvous.OUTPOST_RATE
    kit.configure(
        monkeypatch,
        outposts_calls_per_minute_per_outpost="5",
        outposts_calls_per_minute_per_organisation="1",
    )
    with pytest.raises(rendezvous.NotAdmitted) as refusal:
        await daemon_kit.place()
    assert refusal.value.reason == rendezvous.ORGANISATION_RATE


@pytest.mark.parametrize(
    "name", ["outposts_calls_per_minute_per_outpost", "outposts_calls_per_minute_per_organisation"]
)
def test_a_cap_per_minute_is_at_least_one(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    from choregos_api.config import Settings
    from pydantic import ValidationError

    monkeypatch.setenv(f"CHOREGOS_{name.upper()}", "0")
    with pytest.raises(ValidationError):
        Settings()


def test_the_calls_of_the_last_minute_are_read_through_an_index() -> None:
    """`outpost_calls` keeps a row per call for good: without an index that leads with the outpost and
    the moment a call was placed, every placement would walk all the calls the outpost ever took."""
    from choregos_api.db.models import OutpostCall

    indexed = {
        index.name: [column.name for column in index.columns] for index in OutpostCall.__table__.indexes
    }
    assert indexed["ix_outpost_calls_outpost_created"] == ["outpost_id", "created_at"]
