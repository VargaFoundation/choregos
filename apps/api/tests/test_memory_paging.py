"""The skill library, the knowledge bases and the named memories page in SQL (ADR 0206).

They are ordered by slug, so the cursor is the last slug; what a caller may see is decided in Python
(reach, grants), so a page is filled from batches of rows until it holds `limit` visible ones.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_api.keyset import decode_cursor, encode_cursor

from .agent_scopes_kit import ORG, make_project
from .kits.skill_library import SKILL_MD

SKILLS = f"{ORG}/skills"
BASES = f"{ORG}/knowledge"
MEMORIES = f"{ORG}/memories"


async def _every_slug(client: AsyncClient, url: str, **params: Any) -> list[str]:
    seen: list[str] = []
    cursor: str | None = None
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        got = await client.get(url, params=query)
        assert got.status_code == 200, got.text
        body = got.json()
        seen += [item["slug"] for item in body["items"]]
        if not body["meta"]["has_more"]:
            assert body["meta"]["next_cursor"] is None
            return seen
        cursor = body["meta"]["next_cursor"]


def test_a_text_cursor_round_trips() -> None:
    assert decode_cursor(encode_cursor("billing-api", "id-1")) == ("billing-api", "id-1")


async def test_the_skill_library_is_paged_by_slug(client: AsyncClient, admin: str) -> None:
    names = [f"skill-{index:02d}" for index in range(7)]
    for name in reversed(names):  # created out of order: the page order is the slug's
        made = await client.post(
            SKILLS, json={"files": {"SKILL.md": SKILL_MD.replace("procedure-onboarding", name)}}
        )
        assert made.status_code == 201, made.text
    first = (await client.get(SKILLS, params={"limit": 3})).json()
    assert [s["slug"] for s in first["items"]] == names[:3] and first["meta"]["has_more"] is True
    assert await _every_slug(client, SKILLS, limit=3) == names, "each skill once, in slug order"
    assert await _every_slug(client, SKILLS, q="SKILL-0", limit=2) == names[:7]
    assert await _every_slug(client, SKILLS, q="skill-05") == ["skill-05"]
    assert await _every_slug(client, SKILLS, q="arrival") == names, "the description matches too"
    assert await _every_slug(client, SKILLS, q="%") == [], "LIKE wildcards are characters"
    assert (await client.get(SKILLS, params={"cursor": "not-a-cursor"})).status_code == 422


async def test_the_memories_are_paged_by_slug_and_filtered_in_the_query(
    client: AsyncClient, admin: str
) -> None:
    for index in range(6):
        made = await client.post(
            MEMORIES,
            json={"slug": f"lessons-{index}", "display_name": f"Lessons {index}", "reach": "org"},
        )
        assert made.status_code == 201, made.text
    await client.post(MEMORIES, json={"slug": "alpha", "display_name": "Alpha", "description": "Zebra"})
    expected = ["alpha", *[f"lessons-{index}" for index in range(6)]]
    assert await _every_slug(client, MEMORIES, limit=2) == expected
    assert await _every_slug(client, MEMORIES, q="zebra", limit=2) == ["alpha"], "the description matches"
    assert await _every_slug(client, MEMORIES, scope="global", q="lessons", limit=4) == expected[1:]
    assert await _every_slug(client, MEMORIES, scope="project") == []
    assert await _every_slug(client, MEMORIES, q="_") == [], "LIKE wildcards are characters"


async def test_a_page_of_memories_holds_limit_rows_the_caller_sees(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("tranibor_api.keyset.SCAN_BATCH", 1)  # every batch holds one row, often invisible
    # Python decides who sees a memory: invisible rows in a batch must not shorten a page or end the list.
    for index in range(6):
        made = await client.post(
            MEMORIES, json={"slug": f"m-{index}", "display_name": f"m{index}", "reach": "org"}
        )
        assert made.status_code == 201, made.text
    monkeypatch.setattr(
        "tranibor_api.routers.memories.visible_to",
        lambda _principal, _org, store: store.slug in {"m-1", "m-4", "m-5"},
    )
    pages = []
    cursor = None
    while True:
        body = (
            await client.get(MEMORIES, params={"limit": 2, **({"cursor": cursor} if cursor else {})})
        ).json()
        pages.append([m["slug"] for m in body["items"]])
        cursor = body["meta"]["next_cursor"]
        if not body["meta"]["has_more"]:
            break
    assert pages == [["m-1", "m-4"], ["m-5"]], "a page holds limit visible rows; hidden rows leave no gap"


async def test_a_project_lists_its_implicit_memories_on_the_first_page_only(
    client: AsyncClient, admin: str
) -> None:
    project = await make_project(client, "billing-api")
    for slug in ("decisions", "notes", "runbooks"):
        made = await client.post(
            f"/api/v1/projects/{project['id']}/memories", json={"slug": slug, "display_name": slug}
        )
        assert made.status_code == 201, made.text
    url = f"/api/v1/projects/{project['id']}/memories"
    first = (await client.get(url, params={"limit": 2})).json()
    assert [m["slug"] for m in first["items"]] == ["shared", "own", "decisions", "notes"], (
        "the two implicit memories open the first page, outside its limit"
    )
    assert first["meta"]["has_more"] is True
    second = (await client.get(url, params={"limit": 2, "cursor": first["meta"]["next_cursor"]})).json()
    assert [m["slug"] for m in second["items"]] == ["runbooks"] and second["meta"]["has_more"] is False
    assert await _every_slug(client, url, q="shared") == ["shared"]
    assert await _every_slug(client, url, q="zzz") == []


async def test_the_knowledge_bases_a_caller_may_read_are_paged_by_slug(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await make_project(client, "billing-api")
    for index in range(6):
        made = await client.post(
            BASES,
            json={
                "slug": f"base-{index}",
                "name": f"Base {index}",
                "scope": "projects" if index % 2 else "org",
            }
            | ({"projects": ["billing-api"]} if index % 2 else {}),
        )
        assert made.status_code == 201, made.text
    names = [f"base-{index}" for index in range(6)]
    assert await _every_slug(client, BASES, limit=4) == names
    assert await _every_slug(client, BASES, scope="projects", limit=1) == ["base-1", "base-3", "base-5"]
    assert await _every_slug(client, BASES, q="BASE 2") == ["base-2"], "the name is matched, case aside"
    assert await _every_slug(client, BASES, q="base_2") == [], "a wildcard is matched as written"
    assert await _every_slug(client, BASES, q="base-2") == ["base-2"]
    assert (await client.get(BASES, params={"scope": "everyone"})).status_code == 422
    # Python decides who reads a base: hidden rows between two visible ones do not shorten a page nor end
    # the list early, whatever the batch size.
    monkeypatch.setattr("tranibor_api.keyset.SCAN_BATCH", 1)
    monkeypatch.setattr(
        "tranibor_api.routers.knowledge.bases.visible_to",
        lambda _principal, _org, base, _grants=(): base.scope == "org",
    )
    assert await _every_slug(client, BASES, limit=1) == ["base-0", "base-2", "base-4"]
    page = (await client.get(BASES, params={"limit": 2})).json()
    assert [b["slug"] for b in page["items"]] == ["base-0", "base-2"] and page["meta"]["has_more"] is True


async def test_the_search_routes_return_at_most_k_hits(client: AsyncClient, admin: str) -> None:
    project = await make_project(client, "billing-api")
    url = f"/api/v1/projects/{project['id']}/memory/search"
    assert (await client.get(url, params={"q": "x", "k": 0})).status_code == 422
    assert (await client.get(url, params={"q": "x", "k": 101})).status_code == 422
    assert len((await client.get(url, params={"q": "x", "k": 1})).json()) <= 1
