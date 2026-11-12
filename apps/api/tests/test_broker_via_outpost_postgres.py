# SPDX-License-Identifier: Apache-2.0
"""A call through an outpost on a real PostgreSQL (S65-13, ADR 0148 §6-7 and §10, ADR 0149 §4).

What SQLite cannot show. The call's row is placed under row-level security by a session that names
its outpost for the length of the write, whatever scope the caller has. While the platform waits for
an outpost it holds no pooled connection and no transaction. Two placements that arrive together do
not both take the last call of the minute. The index the cap per minute reads exists in the migrated
schema, and the migration goes down and up again.

Runs only with `CHOREGOS_TEST_DATABASE_URL`: the schema is the migration's, the role is not a
superuser, and no session has a scope it did not ask for (`pg_app`).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from .conftest import APP_ROLE, PG_URL, login, without_postgres
from .kits import outpost_daemon as daemon_kit
from .kits import outpost_route as route
from .kits import outposts as kit
from .kits.outpost_route import ALIAS, BASE, CONNECTOR, ORG, SLUG, TIMEOUT_S

pytestmark = without_postgres

SEARCH = f"{BASE}/{CONNECTOR}__search_issues"


@pytest.fixture(autouse=True)
def door(monkeypatch: pytest.MonkeyPatch) -> kit.Door:
    return route.switch_on(monkeypatch)


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
async def project(client: AsyncClient) -> dict[str, Any]:
    payload = {
        "slug": "billing-api",
        "name": "Billing API",
        "config": {
            "slug": "billing-api",
            "org": kit.ORG,
            "repo": {
                "url": "https://github.com/varga/billing-api.git",
                "default_branch": "main",
                "language": "python",
                "test_command": "make test",
            },
        },
    }
    response = await client.post(f"{ORG}/projects", json=payload)
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _as_the_platform(statement: str, *arguments: Any) -> Any:
    import asyncpg

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        return await connection.fetch(statement, *arguments)
    finally:
        await connection.close()


async def _in_a_transaction() -> list[str]:
    """The application role's connections that hold a transaction open, as the server sees them."""
    rows = await _as_the_platform(
        "SELECT state FROM pg_stat_activity WHERE usename = $1 AND datname = current_database() "
        "AND state LIKE 'idle in transaction%'",
        APP_ROLE,
    )
    return [str(row[0]) for row in rows]


async def test_a_run_whose_scope_is_every_organisation_places_a_row_only_its_outpost_reads(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The run's token gives its session the platform's scope, which names no outpost: under it no
    row of an outpost exists. The route names the connector's outpost for the write, and for it alone."""
    from choregos_api.db.models import OutpostCall
    from choregos_api.db.session import name_the_outpost, session_scope
    from sqlalchemy import select

    fake = route.outpost()
    daemon, headers = await route.prepared(client, project, fake)
    await kit.create(client, "lab")
    async with route.serving(client, daemon, fake):
        answer = await client.post(SEARCH, headers=headers, json={"query": "door"})
    assert answer.json()["status_code"] == 200, answer.text
    here, there = (await kit.outpost_row(SLUG)).id, (await kit.outpost_row("lab")).id
    async with session_scope(orgs="*") as session:
        assert list((await session.execute(select(OutpostCall))).scalars()) == [], "no outpost named"
        await name_the_outpost(session, there)
        assert list((await session.execute(select(OutpostCall))).scalars()) == [], "another outpost"
        await name_the_outpost(session, here)
        kinds = sorted(row.kind for row in (await session.execute(select(OutpostCall))).scalars())
        assert kinds == ["call_tool", "list_tools"]
    # What the call left in the organisation's own tables is read under the organisation's scope.
    assert [line.provider for line in await route.ledger()] == [f"mcp:{CONNECTOR}"]
    assert await route.journal() == [(f"outpost:{SLUG}/{ALIAS}", "broker", "allowed", "", 1)]


async def test_a_call_that_waits_for_an_outpost_holds_no_connection_and_no_transaction(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A read may wait as long as its connector's timeout. A request that kept its transaction open
    for that long would keep a pooled connection `idle in transaction`, and a few calls at once
    would take a replica's pool (#1354)."""
    from choregos_api.db.session import get_engine
    from choregos_api.outposts import rendezvous

    fake = route.outpost()
    _daemon, headers = await route.prepared(client, project, fake, timeout_s=TIMEOUT_S)
    monkeypatch.setattr(rendezvous, "LOOK_S", (60.0, 60.0))  # one look, then only the wait
    started = time.monotonic()
    calling = asyncio.create_task(client.post(SEARCH, headers=headers, json={"query": "door"}))
    try:
        for _ in range(100):
            if rendezvous.waiters().waiting():
                break
            await asyncio.sleep(0.02)
        assert rendezvous.waiters().waiting() == 1, "the call is placed, and waits for its outpost"
        await asyncio.sleep(0.2)
        assert get_engine().pool.checkedout() == 0, "no pooled connection is held while it waits"  # type: ignore[attr-defined]
        assert await _in_a_transaction() == []
        answer = await calling
    finally:
        calling.cancel()
    # Nobody claimed it: it ran out of time by itself, at the connector's timeout, and said so.
    assert TIMEOUT_S - 0.5 <= time.monotonic() - started < TIMEOUT_S + 3
    assert answer.json()["result"]["code"] == "outpost_offline", answer.text


async def test_two_placements_at_once_do_not_both_take_the_last_call_of_the_minute(
    client: AsyncClient,
) -> None:
    """The count of the last minute and the row that takes its last place are one step per
    organisation: a placement that arrives while another has counted and not committed waits for it,
    then counts its row. Two replicas are two sessions here; the lock is the database's."""
    from choregos_api.db.session import session_scope
    from choregos_api.outposts import rendezvous

    await daemon_kit.approved(client)
    quota = rendezvous.Quota(per_outpost=100, per_organisation=100, per_minute_per_outpost=3)
    now = datetime.now(UTC)
    await daemon_kit.place(quota=quota, now=now)
    await daemon_kit.place(quota=quota, now=now)
    row = await kit.outpost_row(SLUG)
    async with session_scope(orgs=[kit.ORG]) as first:
        placed = await rendezvous.place(
            first,
            row,
            call_id="the-last-call-of-the-minute",
            kind=rendezvous.OutpostCallKind.LIST_TOOLS,
            endpoint=ALIAS,
            timeout_s=30,
            quota=quota,
            now=now,
        )
        assert placed.created
        # Written, not committed: another session must not count two calls and pass.
        second = asyncio.create_task(daemon_kit.place(quota=quota, now=now))
        await asyncio.sleep(0.7)
        assert not second.done(), "the second placement waits for the first to commit"
    with pytest.raises(rendezvous.NotAdmitted) as refusal:
        await asyncio.wait_for(second, timeout=10)
    assert refusal.value.reason == rendezvous.OUTPOST_RATE


async def test_two_calls_of_one_run_at_once_are_counted_on_one_line_of_the_journal(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The journal has one line per run, host and decision. Two calls of one run to one alias that
    end together both find none and both write it: one loses, and counts itself on the other's."""
    fake = route.outpost()
    daemon, headers = await route.prepared(client, project, fake)
    async with route.serving(client, daemon, fake):
        answers = await asyncio.gather(
            *(client.post(SEARCH, headers=headers, json={"query": f"door {n}"}) for n in range(4))
        )
    assert [answer.json()["status_code"] for answer in answers] == [200] * 4, [a.text for a in answers]
    assert await route.journal() == [(f"outpost:{SLUG}/{ALIAS}", "broker", "allowed", "", 4)]
    assert len(await route.ledger()) == 4


async def test_the_call_that_loses_the_race_for_the_journal_line_counts_itself_on_it(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """The race itself, held open: one call has written the line and not committed. The other finds
    none, writes it too, waits on the index, is refused when the first commits, and counts itself on
    the line that is there now. Without that, its booking would fail on a line of the journal."""
    from choregos_api.db.session import session_scope
    from choregos_api.services.mcp_gateway_identity import CallIdentity
    from choregos_api.services.outpost_route import DatabaseRendezvous, Placing

    fake = route.outpost()
    await route.prepared(client, project, fake)
    instance, outpost = await route.connector_row(), await kit.outpost_row(SLUG)
    identity = CallIdentity(
        org=kit.ORG,
        org_id=instance.org_id,
        subject=f"run:{route.RUN}",
        purpose="tool_call",
        project="billing-api",
        run=route.RUN,
        agent="coordinator@1",
    )

    async def journaled() -> None:
        async with session_scope(orgs="*") as session:
            await DatabaseRendezvous(Placing(session, outpost, instance, identity))._journal("allowed", "")

    async with session_scope(orgs="*") as first:
        await DatabaseRendezvous(Placing(first, outpost, instance, identity))._journal("allowed", "")
        second = asyncio.create_task(journaled())
        await asyncio.sleep(0.7)
        assert not second.done(), "the second line waits on the first, which is not committed"
    await asyncio.wait_for(second, timeout=10)
    assert await route.journal() == [(f"outpost:{SLUG}/{ALIAS}", "broker", "allowed", "", 2)]


async def test_a_session_that_names_another_outpost_still_finds_the_one_a_connector_routes_to(
    client: AsyncClient,
) -> None:
    """The registry shows one row to a session that names an outpost. A path that worked for one
    outpost and goes on to a connector at another reads the registry with none named, at a call and
    at a declaration alike; what it named is restored."""
    from choregos_api.db.models import Organization
    from choregos_api.db.session import name_the_outpost, session_scope
    from choregos_api.services import outpost_route
    from sqlalchemy import select

    await kit.create(client, SLUG)
    await kit.create(client, "lab")
    await route.declare(client)
    instance = await route.connector_row()
    other = (await kit.outpost_row("lab")).id
    async with session_scope(orgs=[kit.ORG]) as session:
        await name_the_outpost(session, other)
        reached = await outpost_route.route(session, instance)
        assert reached.name == SLUG
        organisation = (await session.execute(select(Organization))).scalar_one()
        declared = await outpost_route.declared_outpost(
            session, organisation, {"outpost": SLUG, "endpoint": ALIAS}, "another"
        )
        assert declared.id == instance.outpost_id
        named = (await session.execute(text("SELECT current_setting('app.current_outpost', true)"))).scalar()
        assert named == other, "what the session named before is restored"


async def test_the_migrated_schema_has_the_index_the_cap_per_minute_reads(client: AsyncClient) -> None:
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        found = (
            await session.execute(
                text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_outpost_calls_outpost_created'")
            )
        ).scalar_one()
    assert "(outpost_id, created_at)" in found
