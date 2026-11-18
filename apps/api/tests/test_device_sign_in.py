"""A developer surface signs in as the person, by the device flow (ADR 0154 §6, S67-05)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any

import pytest
from choregos_api.config import get_settings, reset_settings_cache
from choregos_api.db.models import DeviceAuthorization, Membership, Organization, User, UserSession
from choregos_api.db.session import session_scope
from choregos_api.security import sign_session
from choregos_core import utcnow
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from starlette.requests import HTTPConnection

ALICE = "alice@northwind.dev"
BOB = "bob@northwind.dev"
GRANT = "urn:ietf:params:oauth:grant-type:device_code"


@pytest.fixture(autouse=True)
def _dev_login(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "true")
    reset_settings_cache()
    yield
    reset_settings_cache()


@pytest.fixture
async def anyone(app: Any) -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


@pytest.fixture
async def browsers(app: Any) -> AsyncIterator[Any]:
    opened: list[AsyncClient] = []

    async def open_one(email: str, *, auth_time: int | None = None) -> AsyncClient:
        http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        opened.append(http)
        start = await http.get("/api/v1/auth/login", params={"as": email})
        done = await http.get(start.headers["location"])
        assert done.status_code == 307, done.text
        if auth_time is not None:  # the same session, as if the identity provider had said `auth_time`
            settings = get_settings()
            from choregos_api.security import read_session

            payload = read_session(http.cookies.get(settings.session_cookie) or "", settings)
            assert payload is not None
            http.cookies.set(
                settings.session_cookie, sign_session({**payload, "auth_time": auth_time}, settings)
            )
        return http

    yield open_one
    for http in opened:
        await http.aclose()


async def _start(anyone: AsyncClient, **body: Any) -> dict[str, Any]:
    response = await anyone.post("/api/v1/auth/device", json={"client_kind": "cli", **body})
    assert response.status_code == 200, response.text
    codes: dict[str, Any] = response.json()
    return codes


async def _poll(anyone: AsyncClient, codes: dict[str, Any], *, wait: bool = True) -> Any:
    """One poll; `wait` first lets the interval pass, as a well-behaved surface does."""
    if wait:
        async with session_scope(orgs="*") as session:
            await session.execute(
                update(DeviceAuthorization).values(last_polled_at=utcnow() - timedelta(seconds=60))
            )
    return await anyone.post(
        "/api/v1/auth/device/token", json={"grant_type": GRANT, "device_code": codes["device_code"]}
    )


async def _approve(browser: AsyncClient, codes: dict[str, Any], decision: str = "approve") -> Any:
    return await browser.post(
        f"/api/v1/auth/device/requests/{codes['user_code']}/decision", json={"decision": decision}
    )


async def _signed_in(anyone: AsyncClient, browsers: Any, email: str = ALICE, **start: Any) -> dict[str, Any]:
    """The whole flow: the surface's session (its token response)."""
    codes = await _start(anyone, **start)
    person = await browsers(email)
    assert (await _approve(person, codes)).status_code == 204
    done = await _poll(anyone, codes)
    assert done.status_code == 200, done.text
    token: dict[str, Any] = done.json()
    return token


def _bearer(token: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {token['access_token']}"}


async def test_the_codes_are_shaped_for_a_person_to_read(org: str, anyone: AsyncClient) -> None:
    codes = await _start(anyone, client_name="choregos 0.27.8 on laptop")
    assert len(codes["device_code"]) >= 43  # 256 bits
    assert len(codes["user_code"]) == 9 and codes["user_code"][4] == "-"
    assert not set("AEIOU01IL") & set(codes["user_code"].replace("-", "").replace("L", ""))
    assert codes["verification_uri"].endswith("/device")
    assert codes["verification_uri_complete"] == f"{codes['verification_uri']}?user_code={codes['user_code']}"
    assert codes["expires_in"] == 600 and codes["interval"] == 5
    async with session_scope(orgs="*") as session:
        row = (await session.execute(select(DeviceAuthorization))).scalar_one()
    assert codes["device_code"] not in (row.device_hash, row.user_code_hash), "only hashes are stored"


async def test_an_unapproved_code_is_pending_and_an_unknown_one_is_refused(
    org: str, anyone: AsyncClient
) -> None:
    codes = await _start(anyone)
    pending = await _poll(anyone, codes)
    assert pending.status_code == 400 and pending.json()["error"] == "authorization_pending"
    unknown = await _poll(anyone, {"device_code": "nope"}, wait=False)
    assert unknown.json()["error"] == "invalid_grant"


async def test_polling_faster_than_the_interval_is_told_to_slow_down(org: str, anyone: AsyncClient) -> None:
    codes = await _start(anyone)
    assert (await _poll(anyone, codes)).json()["error"] == "authorization_pending"
    fast = await _poll(anyone, codes, wait=False)
    assert fast.status_code == 400
    assert fast.json() == {
        "error": "slow_down",
        "error_description": "poll no faster than `interval`",
        "interval": 10,
    }
    again = await _poll(anyone, codes, wait=False)
    assert again.json()["interval"] == 15, "each slow_down lengthens the interval by five seconds"


async def test_an_approved_code_gives_a_session_of_the_person_and_only_once(
    org: str, anyone: AsyncClient, browsers: Any
) -> None:
    codes = await _start(anyone, client_kind="editor", client_name="Tranibor for VS Code")
    person = await browsers(ALICE)
    assert (await _approve(person, codes)).status_code == 204
    done = await _poll(anyone, codes)
    assert done.status_code == 200, done.text
    token = done.json()
    assert token["token_type"] == "Bearer" and token["kind"] == "editor"
    assert token["access_token"].startswith("chs_")

    me = await anyone.get("/api/v1/me", headers=_bearer(token))
    assert me.status_code == 200 and me.json()["email"] == ALICE

    replay = await _poll(anyone, codes)
    assert replay.json()["error"] == "expired_token", "a device code is single-use"
    assert (await anyone.get("/api/v1/me", headers=_bearer(token))).status_code == 200


async def test_two_surfaces_holding_the_same_code_cannot_both_win(
    org: str, anyone: AsyncClient, browsers: Any
) -> None:
    codes = await _start(anyone)
    assert (await _approve(await browsers(ALICE), codes)).status_code == 204
    first = await _poll(anyone, codes)
    second = await _poll(anyone, codes)
    assert sorted([first.status_code, second.status_code]) == [200, 400]
    async with session_scope(orgs="*") as session:
        kinds = (await session.execute(select(UserSession.kind))).scalars().all()
    assert sorted(kinds) == ["browser", "cli"]


async def test_a_refused_code_is_access_denied(org: str, anyone: AsyncClient, browsers: Any) -> None:
    codes = await _start(anyone)
    assert (await _approve(await browsers(ALICE), codes, "deny")).status_code == 204
    assert (await _poll(anyone, codes)).json()["error"] == "access_denied"


async def test_an_expired_code_gives_nothing_even_when_approved(
    org: str, anyone: AsyncClient, browsers: Any
) -> None:
    codes = await _start(anyone)
    person = await browsers(ALICE)
    assert (await _approve(person, codes)).status_code == 204
    async with session_scope(orgs="*") as session:
        await session.execute(update(DeviceAuthorization).values(expires_at=utcnow() - timedelta(seconds=1)))
    assert (await _poll(anyone, codes)).json()["error"] == "expired_token"
    assert (await _approve(person, codes)).status_code == 404


async def test_the_session_carries_the_approving_sign_ins_authentication_time(
    org: str, anyone: AsyncClient, browsers: Any
) -> None:
    then = int(time.time()) - 1200
    person = await browsers(ALICE, auth_time=then)
    codes = await _start(anyone)
    assert (await _approve(person, codes)).status_code == 204
    token = (await _poll(anyone, codes)).json()
    listed = (await anyone.get("/api/v1/me/sessions", headers=_bearer(token))).json()
    (surface,) = [s for s in listed if s["kind"] == "cli"]
    stamp = surface["auth_time"].replace("Z", "+00:00")
    from datetime import UTC, datetime

    parsed = datetime.fromisoformat(stamp)
    assert int((parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)).timestamp()) == then
    assert surface["current"] is True and surface["idle_expires_at"] is not None


async def test_a_surface_principal_is_as_old_as_its_approving_sign_in(
    org: str, anyone: AsyncClient, browsers: Any
) -> None:
    """The principal's `authenticated_at` is the approving sign-in's, so a step-up gate sees its real age."""
    from choregos_api.deps import _surface_principal

    then = int(time.time()) - 7200
    person = await browsers(ALICE, auth_time=then)
    codes = await _start(anyone)
    await _approve(person, codes)
    token = (await _poll(anyone, codes)).json()
    async with session_scope(orgs="*") as session:
        connection = HTTPConnection({"type": "http", "headers": [], "client": ("t", 1), "path": "/"})
        principal = await _surface_principal(connection, session, get_settings(), token["access_token"])
    assert principal.credential == "surface" and principal.authenticated_at == then


async def test_ending_the_session_on_my_account_makes_the_next_call_a_401(
    org: str, anyone: AsyncClient, browsers: Any
) -> None:
    person = await browsers(ALICE)
    codes = await _start(anyone)
    await _approve(person, codes)
    token = (await _poll(anyone, codes)).json()
    listed = (await person.get("/api/v1/me/sessions")).json()
    assert {s["kind"] for s in listed} == {"browser", "cli"}
    assert (await person.delete(f"/api/v1/me/sessions/{token['session_id']}")).status_code == 204
    refused = await anyone.get("/api/v1/me", headers=_bearer(token))
    assert refused.status_code == 401
    assert "ended" in refused.text


async def test_the_surface_ends_its_own_session_and_the_browser_keeps_its_own(
    org: str, anyone: AsyncClient, browsers: Any
) -> None:
    person = await browsers(ALICE)
    codes = await _start(anyone)
    await _approve(person, codes)
    token = (await _poll(anyone, codes)).json()
    assert (
        await anyone.delete(f"/api/v1/me/sessions/{token['session_id']}", headers=_bearer(token))
    ).status_code == 204
    assert (await anyone.get("/api/v1/me", headers=_bearer(token))).status_code == 401
    assert (await person.get("/api/v1/me")).status_code == 200


async def test_an_administrators_revocation_ends_a_surface_session_too(
    org: str, anyone: AsyncClient, browsers: Any
) -> None:
    person = await browsers(ALICE)
    codes = await _start(anyone)
    await _approve(person, codes)
    token = (await _poll(anyone, codes)).json()
    assert (await person.post("/api/v1/me/sessions/end-others")).json() == {"ended": 1}
    assert (await anyone.get("/api/v1/me", headers=_bearer(token))).status_code == 401


async def test_a_session_idle_for_too_long_is_refused(org: str, anyone: AsyncClient, browsers: Any) -> None:
    token = await _signed_in(anyone, browsers)
    async with session_scope(orgs="*") as session:
        await session.execute(
            update(UserSession)
            .where(UserSession.kind == "cli")
            .values(idle_expires_at=utcnow() - timedelta(minutes=1))
        )
    assert (await anyone.get("/api/v1/me", headers=_bearer(token))).status_code == 401
    # and it is no longer listed among the person's sessions
    person = await browsers(ALICE)
    assert {s["kind"] for s in (await person.get("/api/v1/me/sessions")).json()} == {"browser"}


async def test_a_use_pushes_the_idle_limit_back(org: str, anyone: AsyncClient, browsers: Any) -> None:
    token = await _signed_in(anyone, browsers)
    soon = utcnow() + timedelta(hours=1)
    async with session_scope(orgs="*") as session:
        await session.execute(
            update(UserSession)
            .where(UserSession.kind == "cli")
            .values(idle_expires_at=soon, last_seen_at=utcnow() - timedelta(hours=2))
        )
    assert (await anyone.get("/api/v1/me", headers=_bearer(token))).status_code == 200
    async with session_scope(orgs="*") as session:
        row = (await session.execute(select(UserSession).where(UserSession.kind == "cli"))).scalar_one()
    idle = row.idle_expires_at
    assert idle is not None
    assert (idle if idle.tzinfo else idle.replace(tzinfo=soon.tzinfo)) > soon + timedelta(days=1)


async def test_a_cookie_is_not_a_surface_credential_nor_the_other_way_round(
    org: str, anyone: AsyncClient, browsers: Any
) -> None:
    person = await browsers(ALICE)
    cookie = person.cookies.get(get_settings().session_cookie) or ""
    assert (
        await anyone.get("/api/v1/me", headers={"Authorization": f"Bearer chs_{cookie}"})
    ).status_code == 401
    token = await _signed_in(anyone, browsers)
    forged = token["access_token"][:-3] + "AAA"
    assert (await anyone.get("/api/v1/me", headers={"Authorization": f"Bearer {forged}"})).status_code == 401
    anyone.cookies.set(get_settings().session_cookie, token["access_token"].removeprefix("chs_"))
    assert (await anyone.get("/api/v1/me")).status_code == 401


async def test_a_sign_in_is_approved_with_the_persons_own_console_session_only(
    org: str, anyone: AsyncClient, browsers: Any
) -> None:
    surface = await _signed_in(anyone, browsers)
    other = await _start(anyone)
    # another surface's session, however valid, does not approve
    refused = await anyone.post(
        f"/api/v1/auth/device/requests/{other['user_code']}/decision",
        json={"decision": "approve"},
        headers=_bearer(surface),
    )
    assert refused.status_code == 403
    # nor does an API token
    person = await browsers(ALICE)
    created = await person.post("/api/v1/me/tokens", json={"name": "script"})
    assert created.status_code == 201, created.text
    by_token = await anyone.post(
        f"/api/v1/auth/device/requests/{other['user_code']}/decision",
        json={"decision": "approve"},
        headers={"Authorization": f"Bearer {created.json()['token']}"},
    )
    assert by_token.status_code == 403
    # nor does nobody
    nobody = await anyone.post(
        f"/api/v1/auth/device/requests/{other['user_code']}/decision", json={"decision": "approve"}
    )
    assert nobody.status_code == 401
    assert (await _poll(anyone, other)).json()["error"] == "authorization_pending"


async def test_the_console_reads_what_a_code_asks_for_before_the_person_approves(
    org: str, anyone: AsyncClient, browsers: Any
) -> None:
    codes = await _start(anyone, client_kind="editor", client_name="Tranibor for VS Code", org="northwind")
    person = await browsers(ALICE)
    seen = await person.get(f"/api/v1/auth/device/requests/{codes['user_code'].lower().replace('-', ' ')}")
    assert seen.status_code == 200, seen.text
    assert seen.json()["client_kind"] == "editor" and seen.json()["org"] == "northwind"
    assert seen.json()["client_name"] == "Tranibor for VS Code"
    assert (await person.get("/api/v1/auth/device/requests/ZZZZ-ZZZZ")).status_code == 404
    assert (await _approve(person, codes)).status_code == 204
    assert (await _approve(person, codes)).status_code == 409


async def _other_organisation_member(email: str, *, subject: str | None) -> None:
    """`email` becomes a member of `other`, bound to `subject` when given (a tenant's own provider)."""
    async with session_scope(orgs="*") as session:
        other = Organization(slug="other", name="Other")
        session.add(other)
        await session.flush()
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        if subject is not None:
            user.oidc_sub = subject.replace("<id>", other.id)
        session.add(Membership(user_id=user.id, org_id=other.id, role="developer"))


async def test_a_session_of_another_organisations_provider_is_refused(
    org: str, anyone: AsyncClient, browsers: Any
) -> None:
    """The request names `other`. Alice, a member of `northwind` only, cannot approve it. Bob, a member of
    `other` whose account is bound to another tenant's provider, cannot either. Carol, bound to `other`'s,
    can."""
    async with session_scope(orgs="*") as session:
        northwind = (
            await session.execute(select(Organization).where(Organization.slug == "northwind"))
        ).scalar_one()
        northwind_id = northwind.id
    alice = await browsers(ALICE)
    bob = await browsers(BOB)
    carol = await browsers("carol@northwind.dev")
    await _other_organisation_member(BOB, subject=f"tenant:{northwind_id}:bob")
    await _other_organisation_member_again("carol@northwind.dev")
    codes = await _start(anyone, org="other")
    assert (await _approve(alice, codes)).status_code == 403
    assert (await _approve(bob, codes)).status_code == 403
    assert (await _poll(anyone, codes)).json()["error"] == "authorization_pending"
    assert (await _approve(carol, codes)).status_code == 204
    assert (await _poll(anyone, codes)).status_code == 200


async def _other_organisation_member_again(email: str) -> None:
    async with session_scope(orgs="*") as session:
        other = (await session.execute(select(Organization).where(Organization.slug == "other"))).scalar_one()
        user = (await session.execute(select(User).where(User.email == email))).scalar_one()
        user.oidc_sub = f"tenant:{other.id}:carol"
        session.add(Membership(user_id=user.id, org_id=other.id, role="developer"))


async def test_a_request_for_an_unknown_organisation_is_refused(org: str, anyone: AsyncClient) -> None:
    response = await anyone.post("/api/v1/auth/device", json={"client_kind": "cli", "org": "nowhere"})
    assert response.status_code == 404


async def test_a_wrong_grant_type_or_kind_is_a_validation_error(org: str, anyone: AsyncClient) -> None:
    assert (await anyone.post("/api/v1/auth/device", json={"client_kind": "web"})).status_code == 422
    bad = await anyone.post("/api/v1/auth/device/token", json={"grant_type": "password", "device_code": "x"})
    assert bad.status_code == 422
