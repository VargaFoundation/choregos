"""`GET /orgs`, `GET /me/tokens` and `GET /me/sessions` page and filter in SQL (ADR 0206)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from tranibor_core.domain import utcnow

ORGS = "/api/v1/orgs"
TOKENS = "/api/v1/me/tokens"
SESSIONS = "/api/v1/me/sessions"


async def _admin_id() -> str:
    from sqlalchemy import select
    from tranibor_api.db.models import User
    from tranibor_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        return (
            await session.execute(select(User.id).where(User.email == "admin@northwind.dev"))
        ).scalar_one()


async def _pages(client: AsyncClient, url: str, key: str, **params: Any) -> list[str]:
    """Every row of a list, followed by cursor to the end; each page is one request."""
    seen: list[str] = []
    cursor: str | None = None
    while True:
        query = {**params, **({"cursor": cursor} if cursor else {})}
        response = await client.get(url, params=query)
        assert response.status_code == 200, response.text
        body = response.json()
        seen += [item[key] for item in body["items"]]
        if not body["meta"]["has_more"]:
            assert body["meta"]["next_cursor"] is None
            return seen
        cursor = body["meta"]["next_cursor"]


# ───────────────────────────── organisations ─────────────────────────────


async def _organisations(count: int) -> None:
    from tranibor_api.db.models import Membership, Organization
    from tranibor_api.db.session import session_scope

    me = await _admin_id()
    async with session_scope(orgs="*") as session:
        for index in range(count):
            other = Organization(slug=f"org-{index:03d}", name=f"Company {count - index}")
            session.add(other)
            await session.flush()
            session.add(Membership(user_id=me, org_id=other.id, role="developer"))


async def test_every_organisation_is_reached_once_in_slug_order(client: AsyncClient, admin: str) -> None:
    # 120 more than the one the administrator already has: more than a page, none skipped or repeated.
    await _organisations(120)
    first = (await client.get(ORGS, params={"limit": 50})).json()
    assert len(first["items"]) == 50 and first["meta"]["has_more"] is True
    seen = await _pages(client, ORGS, "slug", limit=50)
    assert len(seen) == 121 and len(set(seen)) == 121
    assert seen == sorted(seen), "the order is the slug's, as before"
    assert seen[0] == "northwind" or seen[0] < "northwind"


async def test_the_organisation_filters_run_in_the_query(client: AsyncClient, admin: str) -> None:
    await _organisations(30)
    assert await _pages(client, ORGS, "slug", q="ORG-02", limit=4) == [f"org-02{i}" for i in range(10)]
    # the name is matched too: "Company 7" is org-023 (30 - 23), and "Company 17", "Company 27"
    by_name = await _pages(client, ORGS, "slug", q="company 7")
    assert by_name == ["org-023"]
    assert await _pages(client, ORGS, "slug", q="%") == [], "LIKE wildcards are escaped"
    assert await _pages(client, ORGS, "slug", slug="org-007") == ["org-007"]
    assert await _pages(client, ORGS, "slug", slug="org-0") == [], "`slug` is exact, `q` is the substring"
    body = (await client.get(ORGS, params={"slug": "org-007"})).json()
    assert body["items"][0]["role"] == "developer" and body["items"][0]["aliases"] == []


async def test_a_former_slug_finds_the_organisation(client: AsyncClient, admin: str) -> None:
    from tranibor_api.db.models import OrgSlugAlias
    from tranibor_api.db.session import session_scope

    await _organisations(3)
    async with session_scope(orgs="*") as session:
        from sqlalchemy import select
        from tranibor_api.db.models import Organization

        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "org-001"))
        ).scalar_one()
        session.add(OrgSlugAlias(alias="ancien-nom", org_id=org_id))
    assert await _pages(client, ORGS, "slug", slug="ancien-nom") == ["org-001"]
    assert await _pages(client, ORGS, "slug", q="ancien") == ["org-001"]
    (found,) = (await client.get(ORGS, params={"slug": "ancien-nom"})).json()["items"]
    assert found["aliases"] == ["ancien-nom"]


async def test_the_organisations_it_does_not_show_stay_hidden_across_pages(
    client: AsyncClient, admin: str
) -> None:
    from tranibor_api.db.models import Organization
    from tranibor_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        for index in range(60):
            session.add(Organization(slug=f"stranger-{index:03d}", name="not mine"))
    assert await _pages(client, ORGS, "slug", limit=10) == ["northwind"]
    assert await _pages(client, ORGS, "slug", q="stranger") == []


async def test_a_text_cursor_it_did_not_write_is_refused(client: AsyncClient, admin: str) -> None:
    assert (await client.get(ORGS, params={"cursor": "not-a-cursor"})).status_code == 422
    assert (await client.get(ORGS, params={"limit": 0})).status_code == 422


# ───────────────────────────── tokens ─────────────────────────────


async def _tokens(count: int, *, same_instant: bool = False) -> None:
    from tranibor_api.db.models import ApiToken
    from tranibor_api.db.session import session_scope

    me = await _admin_id()
    start = utcnow()
    async with session_scope(orgs="*") as session:
        for index in range(count):
            session.add(
                ApiToken(
                    user_id=me,
                    name=f"token-{index:03d}",
                    hash=f"{index:064d}",
                    scopes=["mcp:read"]
                    if index % 3 == 0
                    else (["mcp:write", "a2a"] if index % 3 == 1 else ["*"]),
                    created_at=start if same_instant else start + timedelta(seconds=index),
                )
            )


@pytest.mark.parametrize("same_instant", [False, True])
async def test_every_token_is_reached_once_page_after_page(
    client: AsyncClient, admin: str, same_instant: bool
) -> None:
    await _tokens(130, same_instant=same_instant)
    first = (await client.get(TOKENS, params={"limit": 50})).json()
    assert len(first["items"]) == 50 and first["meta"]["has_more"] is True
    seen = await _pages(client, TOKENS, "name", limit=50)
    assert len(seen) == 130 and len(set(seen)) == 130
    if not same_instant:
        assert seen[0] == "token-129" and seen[-1] == "token-000", "newest first, as before"


async def test_the_token_filters_run_in_the_query(client: AsyncClient, admin: str) -> None:
    await _tokens(12)
    mcp = await _pages(client, TOKENS, "name", kind="mcp", limit=2)
    assert sorted(mcp) == [f"token-{i:03d}" for i in range(12) if i % 3 != 2]
    api = await _pages(client, TOKENS, "name", kind="api")
    assert sorted(api) == ["token-002", "token-005", "token-008", "token-011"]
    assert await _pages(client, TOKENS, "name", q="TOKEN-00", kind="mcp") == [
        "token-009",
        "token-007",
        "token-006",
        "token-004",
        "token-003",
        "token-001",
        "token-000",
    ]
    assert await _pages(client, TOKENS, "name", q="%") == [], "LIKE wildcards are escaped"
    one = (await client.get(TOKENS, params={"q": "token-004"})).json()["items"][0]
    assert await _pages(client, TOKENS, "name", id=one["id"]) == ["token-004"]
    assert (await client.get(TOKENS, params={"kind": "other"})).status_code == 422


async def test_a_token_page_shows_only_the_callers_tokens(client: AsyncClient, admin: str) -> None:
    from tranibor_api.db.models import ApiToken, User
    from tranibor_api.db.session import session_scope

    await _tokens(3)
    async with session_scope(orgs="*") as session:
        stranger = User(email="stranger@elsewhere.dev")
        session.add(stranger)
        await session.flush()
        session.add(ApiToken(user_id=stranger.id, name="not-mine", hash="f" * 64, scopes=["*"]))
    assert "not-mine" not in await _pages(client, TOKENS, "name")


# ───────────────────────────── sessions ─────────────────────────────


async def _sessions(count: int, *, same_instant: bool = False) -> list[str]:
    from tranibor_api.db.models import UserSession
    from tranibor_api.db.session import session_scope

    me = await _admin_id()
    start = utcnow()
    ids = []
    async with session_scope(orgs="*") as session:
        for index in range(count):
            row = UserSession(
                user_id=me,
                channel="oidc",
                expires_at=start + timedelta(hours=8),
                last_seen_at=None
                if index % 4 == 0
                else (start if same_instant else start + timedelta(seconds=index)),
                created_at=start - timedelta(hours=1) + timedelta(seconds=0 if same_instant else index),
            )
            session.add(row)
            await session.flush()
            ids.append(row.id)
    return ids


@pytest.mark.parametrize("same_instant", [False, True])
async def test_every_session_is_reached_once_page_after_page(
    client: AsyncClient, admin: str, same_instant: bool
) -> None:
    ids = await _sessions(70, same_instant=same_instant)
    first = (await client.get(SESSIONS, params={"limit": 25})).json()
    assert len(first["items"]) == 25 and first["meta"]["has_more"] is True
    seen = await _pages(client, SESSIONS, "id", limit=25)
    # the 70 seeded and the one the sign-in opened; none skipped or repeated
    assert len(seen) == 71 and len(set(seen)) == 71 and set(ids) <= set(seen)


async def test_the_sessions_come_most_recently_active_first(client: AsyncClient, admin: str) -> None:
    await _sessions(9)
    listed = (await client.get(SESSIONS)).json()["items"]
    moments = [row["last_seen_at"] or row["created_at"] for row in listed]
    assert moments == sorted(moments, reverse=True)


async def test_the_sessions_that_no_longer_hold_are_not_paged_in(client: AsyncClient, admin: str) -> None:
    from sqlalchemy import update
    from tranibor_api.db.models import UserSession
    from tranibor_api.db.session import session_scope

    ids = await _sessions(6)
    async with session_scope(orgs="*") as session:
        await session.execute(update(UserSession).where(UserSession.id == ids[0]).values(revoked_at=utcnow()))
        await session.execute(
            update(UserSession)
            .where(UserSession.id == ids[1])
            .values(expires_at=utcnow() - timedelta(minutes=1))
        )
    seen = await _pages(client, SESSIONS, "id", limit=2)
    assert ids[0] not in seen and ids[1] not in seen and len(seen) == 5


async def test_the_current_filter_finds_this_session_whatever_its_page(
    client: AsyncClient, admin: str
) -> None:
    await _sessions(10)
    here = (await client.get(SESSIONS, params={"current": "true"})).json()["items"]
    assert [row["current"] for row in here] == [True]
    others = await _pages(client, SESSIONS, "id", current="false", limit=3)
    assert len(others) == 10 and here[0]["id"] not in others
