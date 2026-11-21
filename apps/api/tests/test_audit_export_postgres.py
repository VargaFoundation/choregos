"""The SIEM export on the real chain (S80-05b, ADR 0207).

PostgreSQL only (`TRANIBOR_TEST_DATABASE_URL`): the migrated schema, the API's non-superuser role under
forced row-level security, and the trigger that links every entry. What SQLite cannot show: that the text the
exporter reads is the text PostgreSQL hashed (a receiver verifies it with SHA-256 alone), that two replicas
never ship one chain at once, and that the cursor's table is the platform's alone.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from tranibor_api.audit_export.exporter import Exporter
from tranibor_api.audit_export.sinks import FakeSink

from .conftest import without_postgres
from .kits.audit_export import check_stream

pytestmark = without_postgres


@pytest.fixture
async def orgs(pg_app: Any) -> dict[str, str]:
    from tranibor_api.db.models import Organization
    from tranibor_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = [Organization(slug=slug, name=slug.upper()) for slug in ("a", "b")]
        session.add_all(rows)
        await session.flush()
        return {row.slug: row.id for row in rows}


async def _write(scope: Any, org_id: str | None, action: str, **payload: Any) -> None:
    from tranibor_api.audit import record
    from tranibor_api.db.session import session_scope

    async with session_scope(orgs=scope) as session:
        await record(session, None, action, org_id=org_id, target_type="test", target_id=action, **payload)


async def _fill(orgs: dict[str, str]) -> None:
    for n in range(3):
        await _write(["a"], orgs["a"], "a.thing", n=n, note='é\n"quoted"', ratio=0.1 + 0.2, big=1.5e-10)
        await _write(["b"], orgs["b"], "b.thing", n=n)
    await _write(["a"], None, "auth.login", who="u-a")
    await _write("*", None, "platform.thing", nested={"z": [1, {"y": None}], "a": True})


async def test_what_the_receiver_gets_is_what_postgres_hashed(orgs: dict[str, str]) -> None:
    """Entries written through `audit.record`, linked by the trigger: the exported `canonical` (read with
    `CAST(payload AS TEXT)`) gives each `hash` under plain SHA-256, for each organisation's chain and the
    platform's, with a payload made to trip a rendering (accents, a quote and a newline, a long number)."""
    await _fill(orgs)
    sink = FakeSink()

    result = await Exporter(sink, batch=2).run_pass()

    assert result.shipped == 8 and result.failed == 0
    assert check_stream([event.as_json() for event in sink.delivered]) == {orgs["a"]: 3, orgs["b"]: 3, "": 2}


async def test_a_restart_ships_only_what_was_written_since(orgs: dict[str, str]) -> None:
    await _fill(orgs)
    first = FakeSink()
    await Exporter(first).run_pass()
    await _write(["a"], orgs["a"], "a.later")
    second = FakeSink()
    await Exporter(second).run_pass()
    assert [(e.org, e.seq) for e in second.delivered] == [("a", 4)]
    assert check_stream([e.as_json() for e in [*first.delivered, *second.delivered]])[orgs["a"]] == 4


async def test_two_replicas_never_ship_one_chain_at_once(orgs: dict[str, str]) -> None:
    """Each replica runs the loop. The cursor's row lock (`FOR UPDATE SKIP LOCKED`) makes the second skip a
    chain the first holds, so a slow sink does not get every batch twice: the entries arrive once each."""
    for n in range(4):
        await _write(["a"], orgs["a"], "a.thing", n=n)

    class Slow(FakeSink):
        async def send(self, events: Any) -> None:
            await asyncio.sleep(0.3)  # the other replica's pass runs while this one holds the chain
            await super().send(events)

    sink = Slow()
    first, second = Exporter(sink, batch=10), Exporter(sink, batch=10)
    results = await asyncio.gather(first.run_pass(), second.run_pass())

    assert sorted(sink.keys()) == sorted(f"{orgs['a']}:{n}" for n in (1, 2, 3, 4))
    assert sum(r.skipped for r in results) == 1
    assert sum(r.shipped for r in results) == 4


async def test_a_crash_between_the_sink_and_the_cursor_ships_the_batch_again(orgs: dict[str, str]) -> None:
    """The sink took the batch, the process died before the cursor moved (here: an error after `send`): the
    transaction is rolled back, the cursor stands, and the next pass sends the batch again."""
    for n in range(3):
        await _write(["a"], orgs["a"], "a.thing", n=n)

    class Crash(FakeSink):
        async def send(self, events: Any) -> None:
            await super().send(events)
            raise RuntimeError("the process was killed")

    sink = Crash()
    with pytest.raises(RuntimeError):
        await Exporter(sink).run_pass()
    assert len(sink.delivered) == 3

    healthy = FakeSink()
    await Exporter(healthy).run_pass()
    assert [e.seq for e in healthy.delivered] == [1, 2, 3]


async def test_the_cursors_are_the_platforms_alone(orgs: dict[str, str]) -> None:
    """Under forced RLS: a session bounded to an organisation reads no cursor and cannot write one."""
    from tranibor_api.db.models import AuditExportCursor
    from tranibor_api.db.session import session_scope

    await _write(["a"], orgs["a"], "a.thing")
    await Exporter(FakeSink()).run_pass()

    async with session_scope(orgs=["a"]) as session:
        assert (await session.execute(select(AuditExportCursor))).scalars().all() == []
    with pytest.raises(DBAPIError):
        async with session_scope(orgs=["a"]) as session:
            await session.execute(
                text(
                    "INSERT INTO audit_export_cursors (sink, chain, seq, hash, updated_at) "
                    "VALUES ('x', 'y', 0, repeat('0', 64), now())"
                )
            )
    async with session_scope(orgs="*") as session:
        assert len((await session.execute(select(AuditExportCursor))).scalars().all()) == 3  # a, b, platform


async def test_an_organisations_deletion_takes_its_cursor(orgs: dict[str, str]) -> None:
    from tranibor_api.db.models import AuditExportCursor, Organization
    from tranibor_api.db.session import session_scope

    await _write(["b"], orgs["b"], "b.thing")
    await Exporter(FakeSink()).run_pass()
    async with session_scope(orgs="*") as session:
        organisation = await session.get(Organization, orgs["b"])
        assert organisation is not None
        await session.delete(organisation)
    async with session_scope(orgs="*") as session:
        chains = {row.chain for row in (await session.execute(select(AuditExportCursor))).scalars()}
    assert orgs["b"] not in chains
