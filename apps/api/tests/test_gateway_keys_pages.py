"""`GET /platform/gateway/keys` pages and filters in SQL (ADR 0206), and the keyset cursor it rests on."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_core.domain import utcnow

URL = "/api/v1/platform/gateway/keys"


async def _seed(count: int, *, project_slug: str | None = None, same_instant: bool = False) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import GatewayKeyRow, Project
    from tranibor_api.db.session import session_scope

    start = utcnow()
    async with session_scope() as session:
        project_id = None
        if project_slug:
            project_id = (
                await session.execute(select(Project.id).where(Project.slug == project_slug))
            ).scalar_one()
        for index in range(count):
            session.add(
                GatewayKeyRow(
                    key_id=f"key-{index:03d}",
                    run_id=f"run-{index:03d}",
                    project_id=project_id if index % 2 == 0 else None,
                    revoked=index % 3 == 0,
                    created_at=start if same_instant else start + timedelta(seconds=index),
                )
            )


async def _all_pages(client: AsyncClient, **params: Any) -> list[str]:
    seen: list[str] = []
    cursor: str | None = None
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        body = (await client.get(URL, params=query)).json()
        seen += [item["key_id"] for item in body["items"]]
        if not body["meta"]["has_more"]:
            assert body["meta"]["next_cursor"] is None
            return seen
        cursor = body["meta"]["next_cursor"]


@pytest.mark.parametrize("same_instant", [False, True])
async def test_every_key_is_reachable_once_page_after_page(
    client: AsyncClient, admin: str, same_instant: bool
) -> None:
    # 230 keys: beyond the 200 the route used to cut at, and with rows sharing one timestamp the
    # primary key alone keeps the order total — no row is skipped or repeated across a page break.
    await _seed(230, same_instant=same_instant)
    first = (await client.get(URL, params={"limit": 50})).json()
    assert len(first["items"]) == 50 and first["meta"]["has_more"] is True
    seen = await _all_pages(client, limit=50)
    assert len(seen) == 230 and len(set(seen)) == 230
    if not same_instant:
        assert seen[0] == "key-229" and seen[-1] == "key-000", "newest first"


async def test_the_filters_run_in_the_query(client: AsyncClient, project: dict[str, Any]) -> None:
    await _seed(12, project_slug="billing-api")
    revoked = await _all_pages(client, state="revoked", limit=2)
    assert sorted(revoked) == ["key-000", "key-003", "key-006", "key-009"]
    active = await _all_pages(client, state="active")
    assert len(active) == 8 and not set(active) & set(revoked)
    assert await _all_pages(client, q="RUN-007") == ["key-007"], "a run id, case-insensitive"
    by_project = await _all_pages(client, q="billing")
    assert sorted(by_project) == [f"key-{index:03d}" for index in range(0, 12, 2)]
    assert await _all_pages(client, q="billing", state="revoked") == ["key-006", "key-000"]
    assert await _all_pages(client, q="%") == [], "LIKE wildcards are escaped"
    item = (await client.get(URL, params={"q": "key-004"})).json()["items"][0]
    assert item["project_slug"] == "billing-api"


async def test_a_cursor_it_did_not_write_is_refused(client: AsyncClient, admin: str) -> None:
    assert (await client.get(URL, params={"cursor": "not-a-cursor"})).status_code == 422
    assert (await client.get(URL, params={"limit": 201})).status_code == 422
    assert (await client.get(URL, params={"state": "expired"})).status_code == 422


def test_the_cursor_round_trips() -> None:
    from tranibor_api.keyset import decode_cursor, encode_cursor

    instant = utcnow()
    assert decode_cursor(encode_cursor(instant, "id-1")) == (instant, "id-1")
