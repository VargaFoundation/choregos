"""The agents' lists page and filter in SQL (ADR 0206): registry, metrics, runs, clients, releases, inbox.

More rows than a page, each reached once, rows sharing one timestamp."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient

from .agent_scopes_kit import ADMIN, ORG, as_user, create_agent

AGENTS = f"{ORG}/agents"


async def _walk(client: AsyncClient, url: str, key: str = "id", **params: Any) -> list[str]:
    """Follow `next_cursor` to the end, asserting each page's shape; returns `key` of every row in order."""
    seen: list[str] = []
    cursor: str | None = None
    while True:
        response = await client.get(url, params={**params, **({"cursor": cursor} if cursor else {})})
        assert response.status_code == 200, response.text
        body = response.json()
        seen += [item[key] for item in body["items"]]
        if not body["meta"]["has_more"]:
            assert body["meta"]["next_cursor"] is None
            return seen
        cursor = body["meta"]["next_cursor"]


async def _agents(client: AsyncClient, count: int, **extra: Any) -> list[str]:
    await as_user(client, ADMIN)
    slugs = [f"agent-{n:03d}" for n in range(count)]
    for slug in slugs:
        assert (await create_agent(client, slug, **extra)).status_code == 201
    return slugs


async def test_the_registry_is_reachable_once_page_after_page(client: AsyncClient, org: str) -> None:
    slugs = await _agents(client, 23)
    first = (await client.get(AGENTS, params={"limit": 10})).json()
    assert len(first["items"]) == 10 and first["meta"]["has_more"] is True
    assert await _walk(client, AGENTS, "slug", limit=10) == slugs, "by name, each once"
    assert (await client.get(AGENTS, params={"limit": 0})).status_code == 422
    assert (await client.get(AGENTS, params={"cursor": "not-a-cursor"})).status_code == 422


async def test_the_registry_filters_run_in_the_query(client: AsyncClient, org: str) -> None:
    await as_user(client, ADMIN)
    for slug, tags in (("alpha", ["x", "y"]), ("beta", ["x"]), ("gamma", [])):
        assert (
            await create_agent(client, slug, tags=tags, display_name=f"The {slug.title()}")
        ).status_code == 201
    assert await _walk(client, AGENTS, "slug", tag=["x"], limit=1) == ["alpha", "beta"]
    assert await _walk(client, AGENTS, "slug", tag=["x", "y"], limit=1) == ["alpha"]
    assert await _walk(client, AGENTS, "slug", q="BET", limit=1) == ["beta"], "slug, case-insensitive"
    assert await _walk(client, AGENTS, "slug", q="the gam") == ["gamma"], "display name"
    assert await _walk(client, AGENTS, "slug", q="%") == [], "LIKE wildcards are escaped"


async def test_a_retired_agent_leaves_the_page_when_asked_to(client: AsyncClient, org: str) -> None:
    await _agents(client, 3)
    retired = await client.post(f"{AGENTS}/agent-001/lifecycle", json={"to": "retired"})
    assert retired.status_code == 200, retired.text
    assert await _walk(client, AGENTS, "slug", retired=False, limit=1) == ["agent-000", "agent-002"]
    assert await _walk(client, AGENTS, "slug", limit=1) == ["agent-000", "agent-001", "agent-002"]


async def test_the_stars_order_survives_a_page_break(client: AsyncClient, org: str) -> None:
    await _agents(client, 6)
    for slug in ("agent-004", "agent-002"):
        assert (await client.put(f"{AGENTS}/{slug}/star")).status_code in (200, 201, 204)
    listed = await _walk(client, AGENTS, "slug", sort="stars", limit=2)
    assert len(listed) == 6 and len(set(listed)) == 6
    assert listed[:2] == ["agent-002", "agent-004"], "starred first, then by name"
    starred = await _walk(client, AGENTS, "slug", starred=True, limit=1)
    assert starred == ["agent-002", "agent-004"]


async def test_the_metrics_page_by_name_with_the_aggregates_of_the_page_only(
    client: AsyncClient, org: str
) -> None:
    slugs = await _agents(client, 7)
    assert await _walk(client, f"{ORG}/agent-metrics", "agent", limit=3) == slugs
    body = (await client.get(f"{ORG}/agent-metrics", params={"limit": 3})).json()
    assert [m["agent"] for m in body["items"]] == slugs[:3] and body["meta"]["has_more"] is True


async def _runs(project: dict[str, Any], count: int, *, same_instant: bool) -> list[str]:
    from tranibor_api.db.models import Run, WorkItem
    from tranibor_api.db.session import session_scope
    from tranibor_core import utcnow

    now = utcnow()
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="BILLING-9", title="T", state="ready")
        session.add(item)
        await session.flush()
        for n in range(count):
            session.add(
                Run(
                    id=f"run-{n:03d}",
                    work_item_id=item.id,
                    project_id=project["id"],
                    stage_role="refine",
                    status="succeeded",
                    agent_slug="writer",
                    agent_version=1,
                    created_at=now if same_instant else now - timedelta(minutes=count - n),
                )
            )
    return [f"run-{n:03d}" for n in range(count)]


@pytest.mark.parametrize("same_instant", [False, True])
async def test_an_agent_s_runs_are_reachable_once_each(
    client: AsyncClient, project: dict[str, Any], same_instant: bool
) -> None:
    await as_user(client, ADMIN)
    await create_agent(client, "writer")
    made = await _runs(project, 25, same_instant=same_instant)
    seen = await _walk(client, f"{AGENTS}/writer/runs", limit=10)
    assert len(seen) == 25 and set(seen) == set(made), "no run skipped or repeated across a page break"
    if not same_instant:
        assert seen == list(reversed(made)), "newest first"


async def test_the_release_history_and_the_inbox_page(client: AsyncClient, org: str) -> None:
    from sqlalchemy import select
    from tranibor_api.db.models import Agent, AgentRelease
    from tranibor_api.db.session import session_scope
    from tranibor_core import utcnow

    await _agents(client, 1)
    now = utcnow()
    async with session_scope() as session:
        agent = (await session.execute(select(Agent).where(Agent.slug == "agent-000"))).scalar_one()
        for n in range(14):
            session.add(
                AgentRelease(
                    org_id=agent.org_id,
                    agent_id=agent.id,
                    channel="stable",
                    kind="promote",
                    version=1,
                    status="pending_approval" if n % 2 == 0 else "done",
                    created_at=now,  # one instant for every row: the id breaks the tie
                )
            )
    history = await _walk(client, f"{AGENTS}/agent-000/releases", limit=5)
    assert len(history) >= 14 and len(set(history)) == len(history)
    inbox = await _walk(client, f"{ORG}/agent-releases", status="pending_approval", limit=3)
    assert len(inbox) == 7 and len(set(inbox)) == 7
    assert (await client.get(f"{ORG}/agent-releases", params={"status": "pending_approval"})).json()["meta"][
        "has_more"
    ] is False


async def test_the_clients_page_by_agent_and_by_creation(client: AsyncClient, org: str) -> None:
    await _agents(client, 3, kind="external")
    for slug in ("agent-000", "agent-001", "agent-002"):
        for n in range(2):
            token = await client.post(
                "/api/v1/me/tokens", json={"name": f"{slug}-{n}", "scopes": ["mcp:read"]}
            )
            attached = await client.post(
                f"{AGENTS}/{slug}/credentials", json={"kind": "token", "token_id": token.json()["id"]}
            )
            assert attached.status_code == 201, attached.text
    everything = await _walk(client, f"{ORG}/agent-credentials", limit=4)
    assert len(everything) == 6 and len(set(everything)) == 6
    mine = await _walk(client, f"{AGENTS}/agent-001/credentials", limit=1)
    assert len(mine) == 2 and len(set(mine)) == 2


def test_the_cursor_round_trips_a_name_and_a_count() -> None:
    from tranibor_api.keyset import decode_cursor, encode_cursor

    assert decode_cursor(encode_cursor("agent-007", "id-1")) == ("agent-007", "id-1")
    assert decode_cursor(encode_cursor(42, "id-1")) == (42, "id-1")
    assert decode_cursor(encode_cursor("42", "id-1")) == ("42", "id-1"), "a name made of digits stays a name"


async def test_a_name_cursor_is_refused_where_a_count_is_expected(client: AsyncClient, org: str) -> None:
    from tranibor_api.keyset import encode_cursor

    await _agents(client, 2)
    by_name = await client.get(AGENTS, params={"limit": 1})
    cursor = by_name.json()["meta"]["next_cursor"]
    assert cursor
    refused = await client.get(AGENTS, params={"sort": "stars", "cursor": cursor})
    assert refused.status_code == 422, refused.text
    by_count = await client.get(AGENTS, params={"sort": "stars", "cursor": encode_cursor(0, "agent-000")})
    assert by_count.status_code == 200, by_count.text
