"""Changing the instance's identity provider: a time-boxed re-link window and its plan (S70-20, ADR 0160).

A new provider gives every person a new subject. While the deployment's window is open
(`CHOREGOS_OIDC_RELINK_UNTIL`), a person's first sign-in through it moves their account, the same row with
its id, memberships and history, to the new subject: only when the provider asserts the address with
`email_verified: true`, only for the ONE account bound to another issuer's subject with that address
(letter case aside), and never twice. The plan (`GET /platform/identity/relink`) says beforehand what the
window will do, by the same rule.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from choregos_api.config import Settings, reset_settings_cache
from choregos_api.db.models import AuditLog, Membership, Organization, User
from choregos_api.db.session import session_scope
from choregos_api.routers.auth import forget_discovery
from httpx import AsyncClient, Response
from sqlalchemy import select

OLD = "https://old-idp.example.test/realms/x"
NEW = "https://new-idp.example.test/realms/y"
PLAN = "/api/v1/platform/identity/relink"


def _id_token(issuer: str, **claims: Any) -> str:
    def b64(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    body = {"iss": issuer, "aud": "choregos"} | claims
    return f"{b64({'alg': 'RS256'})}.{b64(body)}.c2ln"


def _configure(monkeypatch: pytest.MonkeyPatch, issuer: str, until: datetime | None = None) -> None:
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", issuer)
    if until is None:
        monkeypatch.delenv("CHOREGOS_OIDC_RELINK_UNTIL", raising=False)
    else:
        monkeypatch.setenv("CHOREGOS_OIDC_RELINK_UNTIL", until.isoformat())
    reset_settings_cache()
    forget_discovery()


def _in(days: float) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


@pytest.fixture(autouse=True)
def _provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _configure(monkeypatch, NEW, _in(14))
    yield
    reset_settings_cache()
    forget_discovery()


async def _sign_in(client: AsyncClient, userinfo: dict[str, Any], issuer: str = NEW) -> Response:
    """A whole sign-in through `issuer`: the login redirect, then the callback."""
    discovery = {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "userinfo_endpoint": f"{issuer}/userinfo",
    }
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{issuer}/.well-known/openid-configuration").mock(
            return_value=Response(200, json=discovery)
        )
        start = await client.get("/api/v1/auth/login")
        params = parse_qs(urlparse(start.headers["location"]).query)
        token = _id_token(issuer, sub=userinfo.get("sub"), nonce=params["nonce"][0])
        mock.post(f"{issuer}/token").mock(
            return_value=Response(200, json={"access_token": "at", "id_token": token})
        )
        mock.get(f"{issuer}/userinfo").mock(return_value=Response(200, json=userinfo))
        return await client.get("/api/v1/auth/callback", params={"code": "c", "state": params["state"][0]})


async def _account(email: str, sub: str | None, issuer: str | None = OLD, **extra: Any) -> str:
    async with session_scope(orgs="*") as session:
        name = email.split("@", maxsplit=1)[0]
        user = User(email=email, display_name=name, oidc_sub=sub, oidc_issuer=issuer, **extra)
        session.add(user)
        await session.flush()
        return user.id


async def _users() -> dict[str, User]:
    async with session_scope(orgs="*") as session:
        return {u.id: u for u in (await session.execute(select(User))).scalars()}


async def _relinks() -> list[AuditLog]:
    async with session_scope(orgs="*") as session:
        rows = (await session.execute(select(AuditLog).where(AuditLog.action == "user.relink"))).scalars()
        return list(rows)


# ───────────────────────────── the setting ─────────────────────────────


def test_the_former_switch_refuses_to_start_naming_the_window() -> None:
    with pytest.raises(ValueError, match="CHOREGOS_OIDC_RELINK_UNTIL"):
        Settings(oidc_relink_verified_email=True)


def test_a_window_has_a_near_end() -> None:
    with pytest.raises(ValueError, match="more than 31 days"):
        Settings(oidc_relink_until=_in(40))
    assert Settings(oidc_relink_until=_in(30)).relink_window_open()


def test_a_window_without_a_time_zone_is_utc_and_a_past_one_is_closed() -> None:
    naive = Settings(oidc_relink_until=datetime(2020, 1, 1))
    assert naive.oidc_relink_until == datetime(2020, 1, 1, tzinfo=UTC)
    assert not naive.relink_window_open()
    assert not Settings(oidc_relink_until=None).relink_window_open()


# ───────────────────────────── the sign-in ─────────────────────────────


async def test_a_new_provider_moves_the_same_account_while_the_window_is_open(
    client: AsyncClient, org: str
) -> None:
    """The proof the change needs: the same user id before and after, its memberships and its history."""
    user_id = await _account("jo@varga.dev", "old-sub")
    async with session_scope(orgs="*") as session:
        org_id = (await session.execute(select(Organization.id))).scalar_one()
        session.add(Membership(user_id=user_id, org_id=org_id, role="developer"))
        session.add(AuditLog(actor_id=user_id, actor_kind="user", action="work_item.approve", ts=_in(-3)))
    response = await _sign_in(
        client, {"sub": "new-sub", "email": "jo@varga.dev", "email_verified": True, "name": "Jo"}
    )
    assert response.status_code == 307, response.text
    users = await _users()
    assert list(users) == [user_id], "no second account: the same row moved"
    assert (users[user_id].oidc_sub, users[user_id].oidc_issuer) == ("new-sub", NEW)
    async with session_scope(orgs="*") as session:
        memberships = (await session.execute(select(Membership).where(Membership.user_id == user_id))).all()
        history = (await session.execute(select(AuditLog).where(AuditLog.actor_id == user_id))).all()
    assert len(memberships) == 1
    assert len(history) == 1
    (relink,) = await _relinks()
    assert relink.target_id == user_id
    assert relink.payload["previous_sub"] == "old-sub"
    assert relink.payload["previous_issuer"] == OLD
    assert relink.payload["issuer"] == NEW
    me = await client.get("/api/v1/me")
    assert me.json()["id"] == user_id, me.text


async def test_a_second_sign_in_moves_nothing_more(client: AsyncClient, org: str) -> None:
    await _account("jo@varga.dev", "old-sub")
    verified = {"sub": "new-sub", "email": "jo@varga.dev", "email_verified": True}
    assert (await _sign_in(client, verified)).status_code == 307
    assert (await _sign_in(client, verified)).status_code == 307
    assert len(await _relinks()) == 1


async def test_an_account_bound_before_issuers_were_recorded_is_moved(client: AsyncClient, org: str) -> None:
    user_id = await _account("jo@varga.dev", "old-sub", issuer=None)
    response = await _sign_in(client, {"sub": "new-sub", "email": "jo@varga.dev", "email_verified": True})
    assert response.status_code == 307, response.text
    assert (await _users())[user_id].oidc_sub == "new-sub"


@pytest.mark.parametrize(
    "userinfo",
    [
        {"sub": "new-sub", "email": "jo@varga.dev"},
        {"sub": "new-sub", "email": "jo@varga.dev", "email_verified": False},
        {"sub": "new-sub", "email": "jo@varga.dev", "email_verified": "true"},
    ],
    ids=["claim-omitted", "unverified", "not-a-boolean"],
)
async def test_the_window_needs_the_provider_to_assert_the_address_verified(
    client: AsyncClient, org: str, userinfo: dict[str, Any]
) -> None:
    user_id = await _account("jo@varga.dev", "old-sub")
    response = await _sign_in(client, userinfo)
    assert response.status_code == 401, response.text
    users = await _users()
    assert list(users) == [user_id]
    assert users[user_id].oidc_sub == "old-sub"
    assert await _relinks() == []


@pytest.mark.parametrize("until", [None, -1], ids=["no-window", "window-closed"])
async def test_without_an_open_window_nothing_is_moved(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch, until: int | None
) -> None:
    _configure(monkeypatch, NEW, None if until is None else _in(until))
    user_id = await _account("jo@varga.dev", "old-sub")
    response = await _sign_in(client, {"sub": "new-sub", "email": "jo@varga.dev", "email_verified": True})
    assert response.status_code == 401, response.text
    assert "belongs to another identity" in response.text
    assert (await _users())[user_id].oidc_sub == "old-sub"


async def test_an_account_is_never_moved_twice(client: AsyncClient, org: str) -> None:
    """Two identities of the new provider with one address: the first one keeps the account."""
    user_id = await _account("jo@varga.dev", "old-sub")
    first = {"sub": "new-sub", "email": "jo@varga.dev", "email_verified": True}
    assert (await _sign_in(client, first)).status_code == 307
    second = await _sign_in(client, {"sub": "intruder", "email": "jo@varga.dev", "email_verified": True})
    assert second.status_code == 401, second.text
    assert "belongs to another identity" in second.text
    users = await _users()
    assert list(users) == [user_id]
    assert users[user_id].oidc_sub == "new-sub"


async def test_two_accounts_with_one_address_are_refused_both(client: AsyncClient, org: str) -> None:
    first = await _account("jo@varga.dev", "old-1")
    second = await _account("Jo@varga.dev", "old-2")
    response = await _sign_in(client, {"sub": "new-sub", "email": "jo@varga.dev", "email_verified": True})
    assert response.status_code == 401, response.text
    assert "several accounts" in response.text
    users = await _users()
    assert sorted(users) == sorted([first, second]), "no new account either"
    assert {users[first].oidc_sub, users[second].oidc_sub} == {"old-1", "old-2"}
    assert await _relinks() == []


async def test_the_address_matches_whatever_its_letter_case(client: AsyncClient, org: str) -> None:
    user_id = await _account("Jo.Martin@Varga.dev", "old-sub")
    response = await _sign_in(
        client, {"sub": "new-sub", "email": "jo.martin@varga.dev", "email_verified": True}
    )
    assert response.status_code == 307, response.text
    users = await _users()
    assert list(users) == [user_id]
    assert (users[user_id].oidc_sub, users[user_id].email) == ("new-sub", "jo.martin@varga.dev")


@pytest.mark.parametrize(
    ("sub", "extra"),
    [("tenant:some-org:u-1", {}), ("old-sub", {"is_service": True})],
    ids=["a-tenants-member", "a-machine-account"],
)
async def test_the_window_leaves_what_the_instance_provider_does_not_hold(
    client: AsyncClient, org: str, sub: str, extra: dict[str, Any]
) -> None:
    user_id = await _account("jo@varga.dev", sub, **extra)
    response = await _sign_in(client, {"sub": "new-sub", "email": "jo@varga.dev", "email_verified": True})
    assert response.status_code == 401, response.text
    assert (await _users())[user_id].oidc_sub == sub


async def test_every_sign_in_records_the_issuer(client: AsyncClient, org: str) -> None:
    returning = await _account("old@varga.dev", "u-1", issuer=None)
    assert (await _sign_in(client, {"sub": "u-1", "email": "old@varga.dev"})).status_code == 307
    assert (await _sign_in(client, {"sub": "u-2", "email": "new@varga.dev"})).status_code == 307
    users = {u.email: u for u in (await _users()).values()}
    assert users["old@varga.dev"].id == returning
    assert users["old@varga.dev"].oidc_issuer == NEW
    assert users["new@varga.dev"].oidc_issuer == NEW


# ───────────────────────────── the plan ─────────────────────────────


async def _instance() -> dict[str, str]:
    """One account of each kind the plan sorts, besides the administrator's development one."""
    return {
        "pending": await _account("pending@varga.dev", "old-1"),
        "legacy": await _account("legacy@varga.dev", "old-2", issuer=None),
        "twin-a": await _account("twin@varga.dev", "old-3"),
        "twin-b": await _account("Twin@varga.dev", "old-4"),
        "current": await _account("current@varga.dev", "new-1", issuer=NEW),
        "unbound": await _account("invited@varga.dev", None, issuer=None),
        "tenant": await _account("member@tenant.example", "tenant:t:u-1", issuer="https://tenant.example"),
        "machine": await _account("app@machines.local", None, issuer=None, is_service=True),
    }


async def test_the_plan_sorts_every_account_and_changes_nothing(client: AsyncClient, admin: str) -> None:
    ids = await _instance()
    before = {u.id: (u.oidc_sub, u.oidc_issuer) for u in (await _users()).values()}
    response = await client.get(PLAN)
    assert response.status_code == 200, response.text
    plan = response.json()
    assert plan["issuer"] == NEW
    assert plan["window"]["state"] == "open"
    assert plan["window"]["applies"] is True
    assert {a["user_id"] for a in plan["pending"]} == {ids["pending"], ids["legacy"]}
    assert {a["user_id"] for a in plan["ambiguous"]} == {ids["twin-a"], ids["twin-b"]}
    twin = next(a for a in plan["ambiguous"] if a["user_id"] == ids["twin-a"])
    assert twin["same_address_as"] == ["Twin@varga.dev"]
    # The administrator signed in with the development login: an account without a provider subject.
    assert plan["counts"] == {"pending": 2, "ambiguous": 2, "current": 1, "unbound": 2, "not_concerned": 2}
    after = {u.id: (u.oidc_sub, u.oidc_issuer) for u in (await _users()).values()}
    assert after == before


async def test_the_plan_for_another_issuer_says_the_window_does_not_apply(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before the change: the instance still signs in with the old provider, the plan is for the new one."""
    _configure(monkeypatch, OLD)
    ids = await _instance()
    plan = (await client.get(PLAN, params={"issuer": f"{NEW}/"})).json()
    assert plan["issuer"] == NEW, "the trailing slash is not part of the issuer"
    assert plan["configured_issuer"] == OLD
    assert plan["window"] == {"state": "none", "until": None, "applies": False}
    assert ids["current"] not in {a["user_id"] for a in plan["pending"]}
    assert plan["counts"]["pending"] == 2


async def test_what_the_plan_promises_the_sign_in_does(app: Any, client: AsyncClient, admin: str) -> None:
    """The plan and the sign-in read one rule: each pending account moves, each ambiguous one is refused."""
    from httpx import ASGITransport

    from .conftest import login

    await _instance()
    plan = (await client.get(PLAN)).json()
    assert plan["pending"] and plan["ambiguous"]
    for key, expected in (("pending", 307), ("ambiguous", 401)):
        for account in plan[key]:
            userinfo = {"sub": f"new-{account['user_id']}", "email": account["email"], "email_verified": True}
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as person:
                response = await _sign_in(person, userinfo)
            assert response.status_code == expected, (key, response.text)
    users = await _users()
    assert all(users[a["user_id"]].oidc_issuer == NEW for a in plan["pending"])
    assert all(users[a["user_id"]].oidc_issuer == OLD for a in plan["ambiguous"])
    await login(client, admin)
    assert (await client.get(PLAN)).json()["counts"]["pending"] == 0


async def test_the_plan_is_the_platform_administrators(client: AsyncClient, org: str) -> None:
    from .conftest import login

    await login(client, "developer@varga.dev")
    assert (await client.get(PLAN)).status_code == 403


async def test_the_identity_card_says_whether_a_window_is_open(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = {r["key"]: r for r in (await client.get("/api/v1/platform/identity")).json()["items"]}
    assert rows["oidc.relink_window"]["value"].startswith("open until ")
    assert rows["oidc.relink_window"]["source"] == "settings"
    _configure(monkeypatch, NEW, _in(-1))
    rows = {r["key"]: r for r in (await client.get("/api/v1/platform/identity")).json()["items"]}
    assert rows["oidc.relink_window"]["value"].startswith("closed since ")
    _configure(monkeypatch, NEW)
    rows = {r["key"]: r for r in (await client.get("/api/v1/platform/identity")).json()["items"]}
    assert rows["oidc.relink_window"]["value"] == "none"
