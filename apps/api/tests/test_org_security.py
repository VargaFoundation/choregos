# SPDX-License-Identifier: Apache-2.0
"""An organisation demands a recent authentication to approve, and its administrator ends a member's
sessions (ADR 0170). Both are served by the core, with no extension installed."""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from tranibor_api.approval_freshness import is_approval
from tranibor_api.db.models import AuditLog, Membership, User
from tranibor_api.db.session import session_scope
from tranibor_api.errors import ApiError
from tranibor_api.extensions import GestureRequest, check_gesture

ADMIN = "admin@northwind.dev"
MEMBER = "member@northwind.dev"


async def _browser(app: Any, email: str) -> AsyncClient:
    http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    start = await http.get("/api/v1/auth/login", params={"as": email})
    assert start.status_code == 307, start.text
    assert (await http.get(start.headers["location"])).status_code == 307
    return http


async def _drop_memberships(email: str) -> None:
    async with session_scope(orgs="*") as session:
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        for row in (await session.execute(select(Membership).where(Membership.user_id == user.id))).scalars():
            await session.delete(row)


async def test_the_administrator_sets_and_reads_the_freshness(client: AsyncClient, admin: str) -> None:
    assert (await client.get("/api/v1/orgs/northwind/security")).json() == {"approval_freshness_s": None}
    put = await client.put("/api/v1/orgs/northwind/security", json={"approval_freshness_s": 300})
    assert put.status_code == 200, put.text
    assert (await client.get("/api/v1/orgs/northwind/security")).json() == {"approval_freshness_s": 300}
    assert (
        await client.put("/api/v1/orgs/northwind/security", json={"approval_freshness_s": 30})
    ).status_code == 422
    cleared = await client.put("/api/v1/orgs/northwind/security", json={"approval_freshness_s": None})
    assert cleared.status_code == 200
    async with session_scope(orgs="*") as session:
        actions = (await session.execute(select(AuditLog.action))).scalars().all()
    assert "org.security" in actions


async def test_a_non_administrator_cannot_touch_the_security(app: Any, org: str, admin: str) -> None:
    member = await _browser(app, MEMBER)
    try:
        assert (await member.get("/api/v1/orgs/northwind/security")).status_code == 403
        assert (
            await member.put("/api/v1/orgs/northwind/security", json={"approval_freshness_s": 300})
        ).status_code == 403
    finally:
        await member.aclose()


async def _check(org: str, gesture: str, target: dict[str, Any], authenticated_at: int | None) -> None:
    principal = SimpleNamespace(authenticated_at=authenticated_at)
    async with session_scope(orgs="*") as session:
        await check_gesture(session, GestureRequest(gesture, org, principal, target))


async def test_an_old_authentication_cannot_approve_where_the_organisation_asks(
    client: AsyncClient, admin: str, org: str
) -> None:
    approve = {"decision": "approve"}
    await _check(org, "workitem.decision", approve, None)  # nothing asked yet: passes
    await client.put("/api/v1/orgs/northwind/security", json={"approval_freshness_s": 300})
    now = int(time.time())

    await _check(org, "workitem.decision", approve, now - 60)  # recent: passes
    for stale in (now - 3600, None):
        with pytest.raises(ApiError) as refused:
            await _check(org, "workitem.decision", approve, stale)
        assert refused.value.status_code == 401
        assert "reauth=1" in str(refused.value.detail)
    with pytest.raises(ApiError):
        await _check(org, "release.approve", {}, now - 3600)
    with pytest.raises(ApiError):
        await _check(org, "agent.release", {"channel": "stable", "kind": "promote"}, now - 3600)
    # Answering a question, or sending to test, is not approving.
    await _check(org, "workitem.decision", {"decision": "answer"}, now - 3600)
    await _check(org, "agent.release", {"channel": "canary", "kind": "promote"}, now - 3600)


def test_what_counts_as_an_approval() -> None:
    assert is_approval("release.approve", {})
    assert is_approval("workitem.decision", {"decision": "scope_change"})
    assert not is_approval("workitem.decision", {"decision": "reject"})


async def test_the_administrator_ends_every_session_of_a_member(
    app: Any, org: str, admin: str, client: AsyncClient
) -> None:
    laptop = await _browser(app, MEMBER)
    phone = await _browser(app, MEMBER)
    other = await _browser(app, "someone@northwind.dev")
    try:
        assert (await laptop.get("/api/v1/me")).status_code == 200
        denied = await laptop.post(
            f"/api/v1/orgs/northwind/members/{MEMBER}/sessions/revocation", json={"reason": "left the team"}
        )
        assert denied.status_code == 403  # a member cannot revoke, not even their own

        done = await client.post(
            f"/api/v1/orgs/northwind/members/{MEMBER}/sessions/revocation", json={"reason": "left the team"}
        )
        assert done.status_code == 200, done.text
        assert done.json() == {"revoked": MEMBER, "ended": 2}
        assert (await laptop.get("/api/v1/me")).status_code == 401
        assert (await phone.get("/api/v1/me")).status_code == 401
        assert (await other.get("/api/v1/me")).status_code == 200  # nobody else's
        assert (await client.get("/api/v1/me")).status_code == 200
        # A new sign-in afterwards opens a session that holds.
        again = await _browser(app, MEMBER)
        try:
            assert (await again.get("/api/v1/me")).status_code == 200
        finally:
            await again.aclose()
        async with session_scope(orgs="*") as session:
            entry = (
                await session.execute(select(AuditLog).where(AuditLog.action == "user.sessions.revoke"))
            ).scalar_one()
        assert entry.payload["reason"] == "left the team"
    finally:
        for http in (laptop, phone, other):
            await http.aclose()


async def test_a_person_outside_the_organisation_cannot_be_revoked_through_it(
    app: Any, org: str, admin: str, client: AsyncClient
) -> None:
    stranger = await _browser(app, MEMBER)
    await _drop_memberships(MEMBER)  # signed in, but no membership of northwind
    try:
        refused = await client.post(
            f"/api/v1/orgs/northwind/members/{MEMBER}/sessions/revocation", json={"reason": "no reason"}
        )
        assert refused.status_code == 404
        assert (await stranger.get("/api/v1/me")).status_code == 200
    finally:
        await stranger.aclose()
