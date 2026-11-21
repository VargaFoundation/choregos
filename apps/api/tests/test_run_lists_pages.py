"""The lists of a work item and of a run are paged by the server (ADR 0206): runs, run events, artifacts,
timeline, hand-offs and the three lists a runner reads, and the keyset cursor they rest on."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_core.domain import utcnow

PAGE = 4


async def _item(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    created = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Paged", "start": False}
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _pages(client: AsyncClient, url: str, key: Any, **params: Any) -> list[list[Any]]:
    """Every page of `url`, its cursor followed to the end: what `key` reads in each item."""
    pages: list[list[Any]] = []
    cursor: str | None = None
    while True:
        query = {"limit": PAGE, **params, **({"cursor": cursor} if cursor else {})}
        response = await client.get(url, params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"items", "meta"}
        assert len(body["items"]) <= PAGE
        pages.append([key(item) for item in body["items"]])
        if not body["meta"]["has_more"]:
            assert body["meta"]["next_cursor"] is None
            return pages
        cursor = body["meta"]["next_cursor"]
        assert len(pages) < 100, "the cursor never ends"


def _flat(pages: list[list[Any]]) -> list[Any]:
    return [value for page in pages for value in page]


async def _refused(client: AsyncClient, url: str, headers: dict[str, str] | None = None) -> None:
    for params in ({"cursor": "not-a-cursor"}, {"limit": 201}, {"limit": 0}):
        assert (await client.get(url, params=params, headers=headers)).status_code == 422, params


async def test_runs_are_reached_once_oldest_first_even_in_the_same_instant(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from tranibor_api.db.models import Run
    from tranibor_api.db.session import session_scope

    item = await _item(client, project)
    start = utcnow()
    async with session_scope() as session:
        for index in range(11):
            # Seven share the latest instant: the id alone orders them.
            moment = start + timedelta(seconds=index if index < 4 else 10)
            session.add(
                Run(
                    id=f"run-{index:02d}",
                    work_item_id=item["id"],
                    project_id=project["id"],
                    stage_role="write",
                    status="done",
                    created_at=moment,
                )
            )
    url = f"/api/v1/work-items/{item['id']}/runs"
    pages = await _pages(client, url, lambda run: run["id"])
    assert len(pages) == 3 and [len(p) for p in pages] == [4, 4, 3]
    seen = _flat(pages)
    assert sorted(seen) == [f"run-{index:02d}" for index in range(11)] and len(set(seen)) == 11
    assert seen[:4] == ["run-00", "run-01", "run-02", "run-03"], "oldest first"
    await _refused(client, url)


async def test_a_run_log_is_paged_by_seq_and_after_seq_filters_it(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from tranibor_api.db.models import Run, RunEvent
    from tranibor_api.db.session import session_scope

    item = await _item(client, project)
    moment = utcnow()
    async with session_scope() as session:
        session.add(
            Run(
                id="run-log", work_item_id=item["id"], project_id=project["id"], stage_role="w", status="done"
            )
        )
        await session.flush()
        for seq in range(1, 24):
            session.add(RunEvent(run_id="run-log", seq=seq, type="log", payload={}, ts=moment))
    url = "/api/v1/runs/run-log/events"
    pages = await _pages(client, url, lambda event: event["seq"])
    assert _flat(pages) == list(range(1, 24)), "chronological, each once"
    assert _flat(await _pages(client, url, lambda event: event["seq"], after_seq=18)) == [19, 20, 21, 22, 23]
    await _refused(client, url)


async def test_artifacts_are_paged_by_name_then_version(client: AsyncClient, project: dict[str, Any]) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import ArtifactRecord, Organization
    from tranibor_api.db.session import session_scope

    item = await _item(client, project)
    async with session_scope() as session:
        org_id = (await session.execute(select(Organization.id))).scalars().first()
        for name in ("alpha", "beta", "gamma", "delta"):
            for version in (1, 2, 3):
                session.add(
                    ArtifactRecord(
                        org_id=org_id,
                        work_item_id=item["id"],
                        name=name,
                        version=version,
                        type="markdown",
                        kind="markdown",
                        media_type="text/markdown",
                        digest="sha256:" + f"{name}{version}".encode().hex().ljust(64, "0"),
                        size=1,
                        content="x",
                        run_id="run-x",
                    )
                )
    url = f"/api/v1/work-items/{item['id']}/artifacts"
    seen = _flat(await _pages(client, url, lambda a: (a["name"], a["version"])))
    assert seen == sorted((n, v) for n in ("alpha", "beta", "gamma", "delta") for v in (1, 2, 3))
    await _refused(client, url)


async def test_the_timeline_is_paged_in_sql_across_its_sources(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from tranibor_api.db.models import Event, Run
    from tranibor_api.db.session import session_scope
    from tranibor_contracts import EventType

    item = await _item(client, project)
    moment = utcnow()
    async with session_scope() as session:
        for index in range(7):
            # Same instant for all: the source and id keep the order total.
            session.add(
                Event(
                    project_id=project["id"],
                    work_item_id=item["id"],
                    type=str(EventType.WORKITEM_STATE_CHANGED),
                    payload={"from": "a", "to": f"s{index}"},
                    ts=moment,
                )
            )
        for index in range(4):
            session.add(
                Run(
                    id=f"run-t{index}",
                    work_item_id=item["id"],
                    project_id=project["id"],
                    stage_role="w",
                    status="done",
                    started_at=moment,
                )
            )
    url = f"/api/v1/work-items/{item['id']}/timeline"
    pages = await _pages(client, url, lambda entry: (entry["ts"], entry["title"]))
    assert len(pages) >= 3 and all(len(page) <= PAGE for page in pages)
    entries = _flat(pages)
    assert len(entries) == 11
    whole = (await client.get(url, params={"limit": 200})).json()["items"]
    assert [(e["ts"], e["title"]) for e in whole] == entries, "the pages cut the whole, none lost or repeated"
    await _refused(client, url)


async def test_hand_offs_are_paged_by_output(client: AsyncClient, project: dict[str, Any]) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import HandOffReceipt, Organization
    from tranibor_api.db.session import session_scope

    item = await _item(client, project)
    moment = utcnow()
    async with session_scope() as session:
        org_id = (await session.execute(select(Organization.id))).scalars().first()
        for index in range(10):
            for kind in ("produced", "read"):
                session.add(
                    HandOffReceipt(
                        org_id=org_id,
                        work_item_id=item["id"],
                        output=f"out_{index:02d}",
                        digest=f"sha256:{index}",
                        kind=kind,
                        run_id=f"run-{kind}",
                        created_at=moment,
                    )
                )
    url = f"/api/v1/work-items/{item['id']}/hand-offs"
    pages = await _pages(client, url, lambda passage: passage["output"])
    assert [len(p) for p in pages] == [4, 4, 2]
    assert _flat(pages) == [f"out_{index:02d}" for index in range(10)]
    first = (await client.get(url, params={"limit": 3})).json()["items"]
    assert all({e["kind"] for e in p["events"]} == {"produced", "read"} for p in first), (
        "all receipts of an output"
    )
    await _refused(client, url)


async def test_the_inputs_a_runner_reads_are_paged_in_the_declared_order(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import ArtifactRecord, Organization, Run
    from tranibor_api.db.session import session_scope
    from tranibor_api.security import mint_run_token

    item = await _item(client, project)
    names = [f"in_{index:02d}" for index in range(10)]
    async with session_scope() as session:
        org_id = (await session.execute(select(Organization.id))).scalars().first()
        session.add(
            Run(
                id="run-in",
                work_item_id=item["id"],
                project_id=project["id"],
                stage_role="w",
                status="running",
                stage_input={"transition": {"outputs": [], "inputs": names}},
            )
        )
        for name in names:
            session.add(
                ArtifactRecord(
                    org_id=org_id,
                    work_item_id=item["id"],
                    name=name,
                    version=1,
                    type="markdown",
                    kind="markdown",
                    media_type="text/markdown",
                    digest="sha256:" + name.encode().hex().ljust(64, "0"),
                    size=1,
                    content="x",
                    run_id="run-x",
                )
            )
    token = mint_run_token(
        "run-in", project_slug=project["slug"], work_item_key=item["tracker_key"], ttl_minutes=5
    )
    headers = {"Authorization": f"Bearer {token}"}
    url = "/api/v1/internal/runs/run-in/inputs"
    first = await client.get(url, params={"limit": PAGE}, headers=headers)
    assert first.status_code == 200, first.text
    assert [f["name"] for f in first.json()["items"]] == names[:PAGE] and first.json()["meta"]["has_more"]
    cursor, seen = first.json()["meta"]["next_cursor"], names[:PAGE]
    while cursor:
        body = (await client.get(url, params={"limit": PAGE, "cursor": cursor}, headers=headers)).json()
        seen += [f["name"] for f in body["items"]]
        cursor = body["meta"]["next_cursor"]
    assert seen == names, "the runner that follows the cursor gets every input, once"
    await _refused(client, url, headers)


def test_the_keyset_cursor_round_trips_every_kind_and_refuses_a_foreign_one() -> None:
    from tranibor_api.deps import Page
    from tranibor_api.errors import ApiError
    from tranibor_api.keyset import decode_cursor, encode_cursor, position_page

    instant = utcnow()
    assert decode_cursor(encode_cursor(instant, "id-1")) == (instant, "id-1")
    assert decode_cursor(encode_cursor(3, "id-1")) == (3, "id-1")
    assert decode_cursor(encode_cursor(instant, "n", 2)) == (instant, "n", 2)
    with pytest.raises(ApiError) as refused:
        decode_cursor("not-a-cursor")
    assert refused.value.status_code == 422
    with pytest.raises(ApiError):  # a position cursor is a single number
        position_page([1, 2], Page(cursor=encode_cursor(instant, "id-1"), limit=1))
    rows = list(range(10))
    seen: list[int] = []
    cursor: str | None = None
    while True:
        got, meta = position_page(rows, Page(cursor=cursor, limit=3))
        seen += got
        if not meta.has_more:
            break
        cursor = meta.next_cursor
    assert seen == rows
    with pytest.raises(ApiError):
        position_page(rows, Page(cursor=encode_cursor(-1), limit=3))
