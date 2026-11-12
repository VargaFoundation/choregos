"""Row-level security of the outposts' tables, on a real PostgreSQL (S65-11, ADR 0148 §6).

Two settings. `app.current_orgs` is every tenant table's. `app.current_outpost` is the second: the rows
that belong to one outpost (its instances, its enrolment tokens, its calls) exist only for a session
that names that outpost inside an organisation scope that holds it, for reading and for writing, and a
session that names none sees none of them, the platform's scope included. `outposts` is the
organisation's registry: listed under the organisation's scope, narrowed to one row once an outpost is
named. Runs only with `CHOREGOS_TEST_DATABASE_URL`: the schema is the migration's, the role is not a
superuser, and `session_scope()` keeps its real fail-closed behaviour (`pg_app`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from .conftest import login, without_postgres
from .kits import outposts as kit

pytestmark = without_postgres

OWN_ROWS = ("outpost_instances", "outpost_enrolments", "outpost_calls")


@pytest.fixture
async def three_outposts(pg_app: Any) -> dict[str, str]:
    """`a/north` and `a/south` in one organisation, `b/north` in another: each with one instance, one
    enrolment token and one waiting call. The ids, by `org/slug`."""
    from choregos_api.db.models import Organization, Outpost, OutpostCall, OutpostEnrolment, OutpostInstance
    from choregos_api.db.session import name_the_outpost, session_scope

    ids: dict[str, str] = {}
    async with session_scope(orgs="*") as session:
        for org_slug, slugs in (("a", ("north", "south")), ("b", ("north",))):
            org = Organization(slug=org_slug, name=org_slug.upper())
            session.add(org)
            await session.flush()
            for slug in slugs:
                outpost = Outpost(org_id=org.id, slug=slug, display_name=slug, capabilities=["tools"])
                session.add(outpost)
                await session.flush()
                name = f"{org_slug}/{slug}"
                ids[name] = outpost.id
                # Even the platform's scope writes an outpost's rows only under that outpost's name.
                await name_the_outpost(session, outpost.id)
                key = kit.signing_key()
                instance = OutpostInstance(
                    id=outpost.id.replace("-", "") + "0" * 32,
                    org_id=org.id,
                    outpost_id=outpost.id,
                    status="active",
                    signing_key=key,
                    sealing_key=kit.sealing_key(),
                    fingerprint="SHA256:" + "a" * 43,
                    daemon_version="0.27.0",
                )
                session.add(instance)
                session.add(
                    OutpostEnrolment(
                        org_id=org.id,
                        outpost_id=outpost.id,
                        token_digest=(name.encode().hex() * 8)[:64],
                        expires_at=datetime.now(UTC) + timedelta(minutes=15),
                    )
                )
                session.add(
                    OutpostCall(
                        id=f"call-{org_slug}-{slug}",
                        org_id=org.id,
                        outpost_id=outpost.id,
                        kind="call_tool",
                        endpoint="tracker",
                        tool="search_issues",
                        write=False,
                    )
                )
                await session.flush()
                await name_the_outpost(session, None)
    return ids


async def _owners(session: Any, table: str) -> set[str]:
    """Which outposts own the rows a query WITHOUT a `WHERE` clause returns."""
    column = "id" if table == "outposts" else "outpost_id"
    return {str(row[0]) for row in await session.execute(text(f"SELECT {column} FROM {table}"))}


async def test_a_session_that_names_no_outpost_sees_none_of_an_outposts_rows(
    three_outposts: dict[str, str],
) -> None:
    """Fails closed like the first setting: an omission shows nothing."""
    from choregos_api.db.session import session_scope

    a = {three_outposts["a/north"], three_outposts["a/south"]}
    async with session_scope() as session:  # no organisation, no outpost
        for table in ("outposts", *OWN_ROWS):
            assert await _owners(session, table) == set(), table
    async with session_scope(orgs=["a"]) as session:
        assert await _owners(session, "outposts") == a, "the organisation's registry is its administrator's"
        for table in OWN_ROWS:
            assert await _owners(session, table) == set(), f"{table}: an organisation alone is not enough"
    async with session_scope(orgs="*") as session:
        assert await _owners(session, "outposts") == set(three_outposts.values())
        for table in OWN_ROWS:
            assert await _owners(session, table) == set(), f"{table}: there is no every-outpost scope"


async def test_a_session_that_names_an_outpost_reads_no_row_of_another(
    three_outposts: dict[str, str],
) -> None:
    """Of the same organisation or of another, from a query with no `WHERE` clause."""
    from choregos_api.db.session import name_the_outpost, session_scope

    north = three_outposts["a/north"]
    async with session_scope(orgs=["a"]) as session:
        await name_the_outpost(session, north)
        for table in ("outposts", *OWN_ROWS):
            assert await _owners(session, table) == {north}, table
        await name_the_outpost(session, three_outposts["b/north"])  # another organisation's outpost
        for table in ("outposts", *OWN_ROWS):
            assert await _owners(session, table) == set(), f"{table}: the organisation must hold it"
    async with session_scope(orgs=["b"]) as session:
        await name_the_outpost(session, north)
        for table in ("outposts", *OWN_ROWS):
            assert await _owners(session, table) == set(), f"{table}: naming an outpost opens no organisation"
    async with session_scope(orgs="*") as session:
        await name_the_outpost(session, north)
        for table in ("outposts", *OWN_ROWS):
            assert await _owners(session, table) == {north}, f"{table}: one outpost, under any scope"


async def test_a_session_that_names_an_outpost_claims_no_row_of_another(
    three_outposts: dict[str, str],
) -> None:
    """A claim, a revocation and a deletion written with no `WHERE` clause touch the named outpost's
    rows and no other: the policy judges writes as it judges reads."""
    from choregos_api.db.session import name_the_outpost, session_scope

    north = three_outposts["a/north"]
    async with session_scope(orgs=["a"]) as session:
        await name_the_outpost(session, north)
        claimed = await session.execute(
            text("UPDATE outpost_calls SET state = 'claimed', epoch = 0, claimed_at = now()")
        )
        assert claimed.rowcount == 1  # type: ignore[attr-defined]
        revoked = await session.execute(text("UPDATE outpost_instances SET status = 'revoked'"))
        assert revoked.rowcount == 1  # type: ignore[attr-defined]
        deleted = await session.execute(text("DELETE FROM outpost_enrolments"))
        assert deleted.rowcount == 1  # type: ignore[attr-defined]

    for name, outpost_id in three_outposts.items():
        async with session_scope(orgs="*") as session:
            await name_the_outpost(session, outpost_id)
            state = (await session.execute(text("SELECT state FROM outpost_calls"))).scalar_one()
            status = (await session.execute(text("SELECT status FROM outpost_instances"))).scalar_one()
            tokens = (await session.execute(text("SELECT count(*) FROM outpost_enrolments"))).scalar_one()
        expected = ("claimed", "revoked", 0) if outpost_id == north else ("pending", "active", 1)
        assert (state, status, tokens) == expected, name


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO outpost_calls (id, org_id, outpost_id, kind, endpoint, write) "
        "VALUES ('smuggled', :org, :other, 'call_tool', 'tracker', false)",
        "UPDATE outpost_calls SET outpost_id = :other, org_id = :org",
        "UPDATE outpost_instances SET outpost_id = :other, org_id = :org",
        "UPDATE outpost_enrolments SET outpost_id = :other, org_id = :org",
    ],
    ids=["insert-a-call", "move-a-call", "move-an-instance", "move-a-token"],
)
@pytest.mark.parametrize("other", ["a/south", "b/north"])
async def test_a_row_is_not_written_into_another_outpost(
    three_outposts: dict[str, str], statement: str, other: str
) -> None:
    """For writing as for reading: a session that names `a/north` cannot put a row at another outpost,
    of its organisation or of another."""
    from choregos_api.db.models import Outpost
    from choregos_api.db.session import name_the_outpost, session_scope

    async with session_scope(orgs="*") as session:
        target = await session.get(Outpost, three_outposts[other])
        assert target is not None
        values = {"other": target.id, "org": target.org_id}
    with pytest.raises(DBAPIError, match="row-level security"):
        async with session_scope(orgs=["a"]) as session:
            await name_the_outpost(session, three_outposts["a/north"])
            await session.execute(text(statement), values)
    for name, outpost_id in three_outposts.items():
        async with session_scope(orgs="*") as session:
            await name_the_outpost(session, outpost_id)
            for table in OWN_ROWS:
                count = (await session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()
                assert count == 1, f"{name}: {table} holds what it held"


async def test_an_outpost_is_not_created_in_an_organisation_out_of_scope(
    three_outposts: dict[str, str],
) -> None:
    from choregos_api.db.models import Organization, Outpost
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        b = (await session.execute(select(Organization.id).where(Organization.slug == "b"))).scalar_one()
    with pytest.raises(DBAPIError, match="row-level security"):
        async with session_scope(orgs=["a"]) as session:
            session.add(Outpost(org_id=b, slug="intruder", display_name="intruder", capabilities=["tools"]))


async def test_the_outpost_named_is_restored_after_a_block_and_carried_across_a_release(
    three_outposts: dict[str, str],
) -> None:
    """`in_outpost_scope` gives back what was named before; `release_connection` carries the name to
    the session's next transaction, as it carries the organisation scope, and no further."""
    from choregos_api.db.session import (
        allow_release_during_calls,
        in_outpost_scope,
        name_the_outpost,
        outside_the_transaction,
        session_scope,
    )

    north, south = three_outposts["a/north"], three_outposts["a/south"]
    named = "SELECT coalesce(current_setting('app.current_outpost', true), '')"
    async with session_scope(orgs=["a"]) as session:
        async with in_outpost_scope(session, north):
            assert await _owners(session, "outpost_calls") == {north}
        assert (await session.execute(text(named))).scalar_one() == ""
        assert await _owners(session, "outpost_calls") == set(), "after the block, no outpost is named"

        await name_the_outpost(session, south)
        async with in_outpost_scope(session, north):
            assert await _owners(session, "outpost_instances") == {north}
        assert await _owners(session, "outpost_instances") == {south}, "what was named before is restored"

        allow_release_during_calls(session)
        async with outside_the_transaction(session):
            assert not session.in_transaction()
        assert (await session.execute(text(named))).scalar_one() == south
        assert await _owners(session, "outpost_calls") == {south}, "the next transaction names it again"
        await session.commit()
        assert (await session.execute(text(named))).scalar_one() == "", "carried once: transaction-local"


async def test_a_bare_session_is_given_the_scope_of_one_outpost_of_one_organisation(
    three_outposts: dict[str, str],
) -> None:
    """What the door does with a token or a signature in hand (`scope_to_outpost`): afterwards the
    session sees one organisation and one outpost, and never keeps the all-organisations scope."""
    from choregos_api.db.models import Organization
    from choregos_api.db.session import get_sessionmaker
    from choregos_api.services.outposts import scope_to_outpost

    scope = "SELECT current_setting('app.current_orgs', true), current_setting('app.current_outpost', true)"
    north = three_outposts["b/north"]
    async with get_sessionmaker()() as session:
        found = await scope_to_outpost(session, north)
        assert found is not None and (found[0].id, found[1]) == (north, "b")
        assert tuple((await session.execute(text(scope))).one()) == ("b", north)
        assert (await session.execute(select(Organization.slug))).scalars().all() == ["b"]
        for table in ("outposts", *OWN_ROWS):
            assert await _owners(session, table) == {north}, table
        await session.rollback()
    async with get_sessionmaker()() as session:
        unknown = "0192f0c0-1111-7222-8333-444455556666"
        assert await scope_to_outpost(session, unknown) is None
        assert tuple((await session.execute(text(scope))).one()) == ("", unknown)
        assert (await session.execute(select(Organization.slug))).scalars().all() == []
        assert await _owners(session, "outposts") == set()
        await session.rollback()


async def test_through_the_api_an_administrator_manages_the_outposts_of_their_organisation_only(
    pg_app: Any, three_outposts: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The routes name the outpost in their URL before they touch its rows, and an instance enrols from
    a session that had no scope: on the migrated schema, as the application role, with no test shortcut."""
    kit.switch_on(monkeypatch)
    base = "/api/v1/orgs/a/outposts"
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "admin@a.test")
        listed = (await client.get(base)).json()["items"]
        assert [(o["slug"], o["instances"]["active"]) for o in listed] == [("north", 1), ("south", 1)]
        assert (await client.get("/api/v1/orgs/b/outposts")).status_code == 403
        assert (await client.get("/api/v1/orgs/b/outposts/north")).status_code == 403

        created = await client.post(base, json={"slug": "east"})
        assert created.status_code == 201, created.text
        issued = await client.post(f"{base}/east/enrolments")
        assert issued.status_code == 201, issued.text
        enrolled = await kit.enrol(issued.json()["token"])
        assert (enrolled.organisation, enrolled.outpost.slug, enrolled.instance.status) == (
            "a",
            "east",
            "pending",
        )
        instances = (await client.get(f"{base}/east/instances")).json()["items"]
        assert [i["id"] for i in instances] == [enrolled.instance.id]
        approved = await client.post(
            f"{base}/east/instances/{enrolled.instance.id}/approve",
            json={"fingerprint": enrolled.instance.fingerprint},
        )
        assert approved.status_code == 200 and approved.json()["status"] == "active", approved.text
        elsewhere = await client.post(f"{base}/north/instances/{enrolled.instance.id}/revoke")
        assert elsewhere.status_code == 404, "an instance is its own outpost's, whatever the URL says"

        calls = (await client.get(f"{base}/north/calls")).json()["items"]
        assert [c["id"] for c in calls] == ["call-a-north"]
        assert (await client.post(f"{base}/east/revoke")).json()["epoch"] == 1
        assert (await client.delete(f"{base}/east")).status_code == 204
        assert [o["slug"] for o in (await client.get(base)).json()["items"]] == ["north", "south"]
