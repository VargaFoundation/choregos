"""Seven lists used to read up to a cap and cut the page in Python (`Page.slice`, ADR 0206): whatever lay
beyond the cap was unreachable. They read one page from SQL now. Each test seeds MORE rows than the old cap
(1 000 for the audit, 2 000 for the received events, everything else was unbounded in memory), among
them rows sharing one instant, and walks the pages: every row once, the last one reachable.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_core.domain import utcnow

from .conftest import login
from .test_knowledge_rights import _as, _uid

API = "/api/v1"
ROWS = 1030


def _instants(count: int, *, newest: datetime | None = None) -> list[datetime]:
    """`count` instants, newest first, every third one repeated: rows born in the same instant."""
    top = (newest or utcnow()) - timedelta(minutes=5)
    return [top - timedelta(seconds=index // 3) for index in range(count)]


async def _walk(client: AsyncClient, url: str, key: str = "id", **params: Any) -> list[str]:
    seen: list[str] = []
    cursor: str | None = None
    while True:
        query = {"limit": 200, **params, **({"cursor": cursor} if cursor else {})}
        response = await client.get(url, params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        seen += [item[key] for item in body["items"]]
        assert body["meta"]["next_cursor"] not in seen, "the cursor is opaque, not the last row's id"
        if not body["meta"]["has_more"]:
            assert body["meta"]["next_cursor"] is None
            return seen
        cursor = body["meta"]["next_cursor"]


def _once(seen: list[str], expected: set[str]) -> None:
    assert len(seen) == len(set(seen)), "a row came twice"
    assert set(seen) == expected


async def _org_id() -> str:
    from sqlalchemy import select
    from tranibor_api.db.models import Organization
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        return str((await session.execute(select(Organization.id))).scalar_one())


async def test_the_audit_is_reachable_beyond_its_old_cap(client: AsyncClient, admin: str) -> None:
    from tranibor_api.db.models import AuditLog
    from tranibor_api.db.session import session_scope

    org_id = await _org_id()
    async with session_scope() as session:
        rows = [
            AuditLog(org_id=org_id, action="slice.test", target_type="slice", target_id=str(i), ts=ts)
            for i, ts in enumerate(_instants(ROWS))
        ]
        session.add_all(rows)
        await session.flush()
        ids = {row.id for row in rows}
    seen = await _walk(client, f"{API}/audit", target_type="slice")
    _once(seen, ids)
    assert len(seen) > 1000


async def test_the_work_items_are_reachable_and_the_search_runs_in_sql(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from tranibor_api.db.models import WorkItem
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        rows = [
            WorkItem(
                project_id=project["id"],
                tracker_key=f"BILLING-{i}",
                title="Needle 100%" if i % 7 == 0 else f"Task {i}",
                state="ready",
                created_at=ts,
            )
            for i, ts in enumerate(_instants(ROWS))
        ]
        session.add_all(rows)
        await session.flush()
        ids = {row.id for row in rows}
        needles = {row.id for row in rows if row.title.startswith("Needle")}
    url = f"{API}/projects/{project['id']}/work-items"
    _once(await _walk(client, url), ids)
    _once(await _walk(client, url, q="needle 100%"), needles)
    # A literal "%" (the needles' title has one), not a wildcard.
    _once(await _walk(client, url, q="%"), needles)
    assert await _walk(client, url, q="_") == [], "LIKE wildcards are escaped"
    assert await _walk(client, url, q="billing-1030") == []
    sevens = await _walk(client, url, q="BILLING-7", limit=2)
    assert len(sevens) == len(set(sevens)) == 111, "BILLING-7, -70..-79 and -700..-799"


async def test_the_releases_are_reachable(client: AsyncClient, project: dict[str, Any]) -> None:
    from tranibor_api.db.models import Release
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        rows = [
            Release(
                project_id=project["id"],
                env="prod" if i % 2 else "staging",
                batch_no=i,
                status="verified",
                items=[],
                created_at=ts,
            )
            for i, ts in enumerate(_instants(ROWS))
        ]
        session.add_all(rows)
        await session.flush()
        ids = {row.id for row in rows}
        prod = {row.id for row in rows if row.env == "prod"}
    url = f"{API}/projects/{project['id']}/releases"
    _once(await _walk(client, url), ids)
    _once(await _walk(client, url, env="prod"), prod)


async def test_the_received_events_are_reachable_beyond_two_thousand(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from tranibor_api.db.models import ReceivedEvent
    from tranibor_api.db.session import session_scope

    org_id = await _org_id()
    async with session_scope() as session:
        rows = [
            ReceivedEvent(
                org_id=org_id,
                project_id=project["id"],
                source="jira",
                event_type="tracker.item.labeled",
                outcome="ignored",
                received_at=ts,
            )
            for ts in _instants(2030)
        ]
        session.add_all(rows)
        await session.flush()
        ids = {row.id for row in rows}
    seen = await _walk(client, f"{API}/projects/{project['id']}/received-events")
    _once(seen, ids)
    assert len(seen) > 2000


async def test_the_findings_are_reachable_and_a_marked_origin_stays_hidden(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from tranibor_api.db.models import Finding, WorkItem
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        marked = WorkItem(
            project_id=project["id"],
            tracker_key="BILLING-1",
            title="T",
            state="ready",
            restricted_knowledge=["ghost-base"],
        )
        session.add(marked)
        await session.flush()
        rows = [
            Finding(
                project_id=project["id"],
                title=f"F{i}",
                type="bug",
                severity="high" if i % 2 else "low",
                evidence="e",
                status="pending",
                created_at=ts,
                # every fifth finding comes out of the marked work item
                origin_work_item_id=marked.id if i % 5 == 0 else None,
            )
            for i, ts in enumerate(_instants(ROWS))
        ]
        session.add_all(rows)
        await session.flush()
        everything = {row.id for row in rows}
        open_ones = {row.id for row in rows if row.origin_work_item_id is None}
        high = {row.id for row in rows if row.severity == "high"}
    url = f"{API}/projects/{project['id']}/findings"
    # The administrator reads the marked work item, so every finding.
    _once(await _walk(client, url), everything)
    _once(await _walk(client, url, severity="high"), high)
    # A project member who does not read the mark sees none of its findings, on every page.
    async with _as(client, "dev@northwind.dev", projects={"billing-api": "viewer"}) as member:
        seen = await _walk(member, url, limit=50)
        _once(seen, open_ones)


async def test_the_projects_page_by_slug_and_hide_the_unreadable(client: AsyncClient, admin: str) -> None:
    from tranibor_api.db.models import Project
    from tranibor_api.db.session import session_scope

    org_id = await _org_id()
    async with session_scope() as session:
        rows = [Project(org_id=org_id, slug=f"p-{i:03d}", name=f"P{i}") for i in range(230)]
        session.add_all(rows)
        await session.flush()
    slugs = await _walk(client, f"{API}/orgs/northwind/projects", key="slug", limit=100)
    assert slugs == sorted(slugs) and len(slugs) == len(set(slugs)) == 230
    # Without a role on the organisation, only the projects where one's own role reads.
    await _uid(client, "reader@northwind.dev", projects={"p-007": "viewer", "p-200": "viewer"})
    async with _as(client, "reader@northwind.dev", projects={"p-007": "viewer", "p-200": "viewer"}) as reader:
        assert await _walk(reader, f"{API}/orgs/northwind/projects", key="slug", limit=1) == [
            "p-007",
            "p-200",
        ]


async def test_my_conversations_are_reachable_newest_first(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import AgentSessionRecord, User
    from tranibor_api.db.session import session_scope

    org_id = await _org_id()
    await login(client, "admin@northwind.dev")
    async with session_scope() as session:
        user_id = str(
            (await session.execute(select(User.id).where(User.email == "admin@northwind.dev"))).scalar_one()
        )
        instants = _instants(ROWS)
        rows = [
            AgentSessionRecord(
                org_id=org_id,
                project_id=project["id"],
                agent_slug="a",
                agent_version=1,
                user_id=user_id,
                kind="conversation",
                status="open",
                created_at=instants[-1],  # every row was created long before its last activity
                last_activity_at=ts if i % 2 else None,
                expires_at=utcnow() + timedelta(hours=1),
            )
            for i, ts in enumerate(instants)
        ]
        session.add_all(rows)
        await session.flush()
        ids = {row.id for row in rows}
    seen = await _walk(client, f"{API}/me/conversations")
    _once(seen, ids)


def test_a_string_cursor_round_trips() -> None:
    from tranibor_api.keyset import decode_cursor, encode_cursor

    assert decode_cursor(encode_cursor("p-007", "id-1")) == ("p-007", "id-1")


@pytest.mark.parametrize("cursor", ["x", "WyJ4Il0", "WyJ0IiwgNSwgIngiXQ"])
async def test_a_cursor_it_did_not_write_is_refused(client: AsyncClient, admin: str, cursor: str) -> None:
    assert (await client.get(f"{API}/orgs/northwind/projects", params={"cursor": cursor})).status_code == 422
