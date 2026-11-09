"""A browser session is recorded: its owner sees it and ends it, from any other one (ADR 0130)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from choregos_api.config import get_settings, reset_settings_cache
from choregos_api.db.models import AuditLog, User, UserSession
from choregos_api.db.session import session_scope
from choregos_api.security import sign_session
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

ALICE = "alice@varga.dev"
LINUX_FIREFOX = "Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0"
BOB = "bob@varga.dev"


@pytest.fixture(autouse=True)
def _dev_login(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "true")
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
async def browsers(app: Any) -> AsyncIterator[Any]:
    """A factory of browsers: each its own cookie jar and `User-Agent`, on the same API."""
    opened: list[AsyncClient] = []

    async def open_one(email: str, agent: str = LINUX_FIREFOX) -> AsyncClient:
        http = AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test", headers={"user-agent": agent}
        )
        opened.append(http)
        start = await http.get("/api/v1/auth/login", params={"as": email})
        assert start.status_code == 307, start.text
        done = await http.get(start.headers["location"])
        assert done.status_code == 307, done.text
        return http

    yield open_one
    for http in opened:
        await http.aclose()


async def _sessions(browser: AsyncClient) -> list[dict[str, Any]]:
    response = await browser.get("/api/v1/me/sessions")
    assert response.status_code == 200, response.text
    sessions: list[dict[str, Any]] = response.json()
    return sessions


async def test_a_sign_in_is_listed_as_this_session_with_its_browser(org: str, browsers: Any) -> None:
    laptop = await browsers(ALICE)
    (only,) = await _sessions(laptop)
    assert only["current"] is True
    assert only["channel"] == "oidc"
    assert "Firefox" in only["user_agent"]
    assert only["last_seen_at"] is not None
    assert only["expires_at"] > only["created_at"]


async def test_ending_a_session_signs_that_browser_out_and_only_it(org: str, browsers: Any) -> None:
    laptop = await browsers(ALICE)
    phone = await browsers(ALICE, agent="Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) Safari/605.1")
    listed = await _sessions(laptop)
    assert len(listed) == 2
    other = next(s for s in listed if not s["current"])
    assert "iPhone" in other["user_agent"]

    assert (await laptop.delete(f"/api/v1/me/sessions/{other['id']}")).status_code == 204

    refused = await phone.get("/api/v1/me")
    assert refused.status_code == 401
    assert "ended" in refused.text
    assert (await laptop.get("/api/v1/me")).status_code == 200
    assert [s["current"] for s in await _sessions(laptop)] == [True]


async def test_ending_the_other_sessions_keeps_this_one(org: str, browsers: Any) -> None:
    laptop = await browsers(ALICE)
    phone = await browsers(ALICE)
    tablet = await browsers(ALICE)
    someone_else = await browsers(BOB)

    response = await laptop.post("/api/v1/me/sessions/end-others")
    assert response.status_code == 200, response.text
    assert response.json() == {"ended": 2}

    assert (await phone.get("/api/v1/me")).status_code == 401
    assert (await tablet.get("/api/v1/me")).status_code == 401
    assert (await laptop.get("/api/v1/me")).status_code == 200
    # Another person's sessions are theirs.
    assert (await someone_else.get("/api/v1/me")).status_code == 200
    async with session_scope(orgs="*") as session:
        actions = (await session.execute(select(AuditLog.action))).scalars().all()
    assert "session.end_others" in actions


async def test_someone_elses_session_cannot_be_ended(org: str, browsers: Any) -> None:
    alice = await browsers(ALICE)
    bob = await browsers(BOB)
    (bobs,) = await _sessions(bob)
    response = await alice.delete(f"/api/v1/me/sessions/{bobs['id']}")
    assert response.status_code == 404
    assert (await bob.get("/api/v1/me")).status_code == 200


async def test_signing_out_ends_the_session_on_the_server(org: str, browsers: Any) -> None:
    """A copy of the cookie, taken before signing out, no longer opens anything."""
    laptop = await browsers(ALICE)
    settings = get_settings()
    copied = laptop.cookies.get(settings.session_cookie)
    assert (await laptop.post("/api/v1/auth/logout")).status_code == 204

    async with AsyncClient(transport=laptop._transport, base_url="http://test") as thief:
        thief.cookies.set(settings.session_cookie, copied or "")
        assert (await thief.get("/api/v1/me")).status_code == 401


async def _older_cookie(email: str) -> str:
    """A cookie opened before sessions were recorded: no `sid`."""
    async with session_scope(orgs="*") as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
    now = int(time.time())
    return sign_session({"sub": user.id, "iat": now - 60, "exp": now + 3600}, get_settings())


async def test_an_older_cookie_holds_until_its_owner_ends_the_other_sessions(
    org: str, browsers: Any, app: Any
) -> None:
    laptop = await browsers(ALICE)
    settings = get_settings()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as old:
        old.cookies.set(settings.session_cookie, await _older_cookie(ALICE))
        assert (await old.get("/api/v1/me")).status_code == 200
        # Unrecorded, it is not listed; the list does not pretend to know it.
        assert all(s["current"] is False for s in await _sessions(old))

        assert (await laptop.post("/api/v1/me/sessions/end-others")).status_code == 200
        assert (await old.get("/api/v1/me")).status_code == 401
        assert (await laptop.get("/api/v1/me")).status_code == 200


async def test_ending_the_others_from_an_older_cookie_keeps_that_browser_signed_in(
    org: str, browsers: Any, app: Any
) -> None:
    phone = await browsers(ALICE)
    settings = get_settings()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as old:
        old.cookies.set(settings.session_cookie, await _older_cookie(ALICE))
        response = await old.post("/api/v1/me/sessions/end-others")
        assert response.status_code == 200, response.text
        assert response.json() == {"ended": 1}

        assert (await phone.get("/api/v1/me")).status_code == 401
        assert (await old.get("/api/v1/me")).status_code == 200
        (renewed,) = await _sessions(old)
        assert renewed["current"] is True
        assert renewed["channel"] == "renewed"


async def test_an_expired_session_row_refuses_its_cookie(org: str, browsers: Any) -> None:
    laptop = await browsers(ALICE)
    async with session_scope(orgs="*") as session:
        for row in (await session.execute(select(UserSession))).scalars():
            row.expires_at = row.created_at
    assert (await laptop.get("/api/v1/me")).status_code == 401
