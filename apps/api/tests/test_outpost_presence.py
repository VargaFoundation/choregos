# SPDX-License-Identifier: Apache-2.0
"""Whether an outpost is there to take a call (S65-13, ADR 0149 §7 and §10).

Presence is read from what the last polls of an outpost's instances wrote: an approved instance that
polled within the poll window is online, and it would be handed a call for the aliases it announced,
if it announces `tools`. A connector's route reads it to fail fast, and writes nothing, when nobody
is there; an agent's access tab will say "offline" from the same function.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from .kits import outpost_daemon as daemon_kit
from .kits import outposts as kit
from .kits.outpost_daemon import SLUG

NOW = datetime(2026, 11, 12, 9, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def door(monkeypatch: pytest.MonkeyPatch) -> kit.Door:
    daemon_kit.key_wrapper(monkeypatch)
    return kit.switch_on(monkeypatch)


async def _presence(now: datetime = NOW) -> Any:
    from choregos_api.db.session import session_scope
    from choregos_api.services import outposts as service

    outpost = await kit.outpost_row(SLUG)
    async with session_scope(orgs=[kit.ORG]) as session:
        return await service.presence(session, outpost, now=now)


async def _instance(**row: Any) -> str:
    """An instance of the outpost as its last poll left it, written by hand."""
    from choregos_api.db.models import OutpostInstance

    (daemon,) = await daemon_kit.instances(1)
    seen = {"last_seen_at": NOW, "endpoints": ["tracker"], "capabilities": ["tools"]} | row
    await daemon_kit.change(OutpostInstance, str(daemon.instance_id), **seen)
    return str(daemon.instance_id)


async def test_an_outpost_nobody_enrolled_at_is_offline(client: AsyncClient, admin: str) -> None:
    await kit.create(client, SLUG)
    found = await _presence()
    assert not found.online and found.endpoints == frozenset() and not found.serves("tracker")


async def test_an_instance_that_polled_within_the_window_serves_the_aliases_it_announced(
    client: AsyncClient, admin: str
) -> None:
    from choregos_api.services import outposts as service

    await kit.create(client, SLUG)
    await _instance(endpoints=["tracker", "forge"])
    found = await _presence()
    assert found.online and found.endpoints == {"tracker", "forge"}
    assert found.serves("tracker") and not found.serves("wiki")
    # The window is the one the outposts' page reads: two polls and a margin.
    edge = NOW + timedelta(seconds=service.POLL_WINDOW_S)
    assert (await _presence(edge)).online
    late = await _presence(edge + timedelta(seconds=1))
    assert not late.online and not late.serves("tracker")


@pytest.mark.parametrize("status", ["pending", "revoked"])
async def test_an_instance_that_is_not_approved_is_not_there(
    client: AsyncClient, admin: str, status: str
) -> None:
    await kit.create(client, SLUG)
    await _instance(status=status)
    found = await _presence()
    assert not found.online and not found.serves("tracker")


async def test_an_instance_that_does_not_announce_tools_is_online_and_serves_nothing(
    client: AsyncClient, admin: str
) -> None:
    await kit.create(client, SLUG)
    await _instance(capabilities=[])
    found = await _presence()
    assert found.online and not found.serves("tracker")
    # Another instance of the same outpost that does: the outpost serves what that one announces.
    await _instance(endpoints=["forge"])
    assert (await _presence()).endpoints == {"forge"}


async def test_an_instance_that_never_polled_is_not_there(client: AsyncClient, admin: str) -> None:
    await kit.create(client, SLUG)
    await _instance(last_seen_at=None)
    assert not (await _presence()).online


async def test_presence_reads_what_a_poll_wrote_a_moment_ago_in_another_session(
    client: AsyncClient, admin: str
) -> None:
    """Columns are read, not the rows a session already holds: a session that loaded an instance
    before its last poll does not answer from what it loaded."""
    from choregos_api.db.models import OutpostInstance
    from choregos_api.db.session import in_outpost_scope, session_scope
    from choregos_api.services import outposts as service
    from sqlalchemy import select

    await kit.create(client, SLUG)
    instance_id = await _instance(last_seen_at=None)
    outpost = await kit.outpost_row(SLUG)
    async with session_scope(orgs=[kit.ORG]) as session:
        async with in_outpost_scope(session, outpost.id):
            held = (await session.execute(select(OutpostInstance))).scalars().all()
        assert [row.last_seen_at for row in held] == [None]
        await daemon_kit.change(OutpostInstance, instance_id, last_seen_at=NOW)
        assert (await service.presence(session, outpost, now=NOW)).serves("tracker")
