"""A member links their own forge account, proven by the forge's OAuth sign-in (S66-03, ADR 0154).

An organisation administrator configures a forge's OAuth application as a `forge_account` connector; a
member starts the link (a state signed for them and their session, a PKCE verifier in a cookie only the
callback receives), signs in at the forge (the fake forge plays it), and the callback records the forge's
numeric user id as a VERIFIED link. An administrator may still declare one: it is stored NOT verified. One
forge account belongs to one member of the organisation, whichever way it was linked.
"""

from __future__ import annotations

import base64
import hashlib
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from choregos_adapters.fakes.forge_accounts import FakeForge
from httpx import AsyncClient
from sqlalchemy import select

from .conftest import login

ORG = "/api/v1/orgs/varga"


@pytest.fixture
def forge() -> Iterator[FakeForge]:
    from choregos_api.services.linked_accounts import set_forge_transport

    fake = FakeForge()
    set_forge_transport(fake.transport())
    yield fake
    set_forge_transport(None)


async def _member(client: AsyncClient, email: str, role: str = "developer") -> str:
    added = await client.post(f"{ORG}/members", json={"email": email, "role": role})
    assert added.status_code == 201, added.text
    return str(added.json()["user_id"])


async def _configure(client: AsyncClient, forge: FakeForge, name: str = "github", **config: Any) -> None:
    """The administrator's gesture: the forge's OAuth application, its secret sealed, its hosts opened."""
    provider = config.pop("provider", "github")
    body = {
        "name": name,
        "kind": "forge_account",
        "type": f"{provider}-oauth",
        "config": {"client_id": forge.client_id, **config},
        "secret_values": {"client_secret": forge.client_secret},
        "allow_egress": True,
    }
    created = await client.post(f"{ORG}/connectors", json=body)
    assert created.status_code == 201, created.text


async def _start(client: AsyncClient, instance: str = "github", provider: str = "github") -> str:
    started = await client.post(
        f"/api/v1/me/linked-accounts/{provider}/start", params={"org": "varga", "instance": instance}
    )
    assert started.status_code == 200, started.text
    return str(started.json()["authorization_url"])


async def _callback(client: AsyncClient, redirect: str) -> dict[str, list[str]]:
    """The browser follows the forge's redirect: the callback answers with the console page to open."""
    parsed = urlparse(redirect)
    done = await client.get(f"{parsed.path}?{parsed.query}")
    assert done.status_code == 303, done.text
    landing = urlparse(done.headers["location"])
    assert landing.path == "/account" and landing.fragment == "linked-accounts"
    return parse_qs(landing.query)


async def _mine(client: AsyncClient) -> dict[str, Any]:
    listed = await client.get("/api/v1/me/linked-accounts")
    assert listed.status_code == 200, listed.text
    return dict(listed.json())


async def _audit(action: str) -> list[Any]:
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return list((await session.execute(select(AuditLog).where(AuditLog.action == action))).scalars())


async def test_a_member_links_their_github_account_and_it_is_verified(
    client: AsyncClient, admin: str, forge: FakeForge
) -> None:
    await _member(client, "dev@varga.dev")
    await _configure(client, forge)
    forge.user("devon", 583231)

    await login(client, "dev@varga.dev")
    offered = await _mine(client)
    assert offered["items"] == []
    assert offered["instances"] == [
        {"org": "varga", "instance": "github", "provider": "github", "base_url": "https://github.com"}
    ]
    url = await _start(client)
    query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert query["scope"] == "read:user" and query["code_challenge_method"] == "S256"
    assert query["redirect_uri"].endswith("/api/v1/auth/linked-accounts/callback")
    cookie = next(c for c in client.cookies.jar if c.name == "choregos_forge_link")
    assert cookie.path == "/api/v1/auth/linked-accounts", "only the callback receives the PKCE verifier"
    # The verifier in the cookie is the one the challenge was derived from: the forge checks it.
    assert query["code_challenge"] not in str(cookie.value)

    landed = await _callback(client, forge.authorize(url, "devon"))

    assert landed == {"linked": ["github"]}
    [link] = (await _mine(client))["items"]
    assert link["external_id"] == "583231" and link["login"] == "devon"
    assert link["verified"] is True and link["verified_at"] and link["connector"] == "github"
    assert link["base_url"] == "https://github.com" and link["profile_url"] == "https://github.com/devon"
    assert link["avatar_url"] and link["created_by"] == "dev@varga.dev"
    exchange = parse_qs(forge.requests[0].content.decode())
    assert exchange["client_secret"] == [forge.client_secret], "the sealed secret reached the token endpoint"
    challenge = base64.urlsafe_b64encode(hashlib.sha256(exchange["code_verifier"][0].encode()).digest())
    assert challenge.decode().rstrip("=") == query["code_challenge"]
    [audited] = await _audit("linked_account.verify")
    assert audited.payload["external_id"] == "583231" and audited.payload["provider"] == "github"
    assert "token" not in str(audited.payload)


async def test_a_state_that_is_not_this_sessions_is_refused_before_the_forge_is_called(
    client: AsyncClient, admin: str, forge: FakeForge
) -> None:
    await _member(client, "dev@varga.dev")
    await _member(client, "eve@varga.dev")
    await _configure(client, forge)

    await login(client, "dev@varga.dev")
    url = await _start(client)
    redirect = forge.authorize(url, "devon")
    parsed = urlparse(redirect)
    params = parse_qs(parsed.query)

    # A state that is not the one signed for this handshake.
    other = parse_qs(urlparse(await _start(client)).query)["state"][0]
    client.cookies.delete("choregos_forge_link")
    await _start(client)  # the cookie of a third handshake
    forged = f"{parsed.path}?code={params['code'][0]}&state={other}"
    assert await _callback(client, f"https://api.test{forged}") == {"link_error": ["handshake"]}
    # A tampered state.
    tampered = f"{parsed.path}?code={params['code'][0]}&state={params['state'][0][:-2]}xx"
    assert await _callback(client, f"https://api.test{tampered}") == {"link_error": ["handshake"]}
    # Another person, in their own session, replaying the redirect meant for dev.
    await login(client, "eve@varga.dev")
    assert await _callback(client, redirect) == {"link_error": ["handshake"]}
    # The forge said no.
    await login(client, "dev@varga.dev")
    denied = f"{parsed.path}?error=access_denied&state={params['state'][0]}"
    assert await _callback(client, f"https://api.test{denied}") == {"link_error": ["denied"]}

    assert forge.requests == [], "no code was exchanged"
    assert (await _mine(client))["items"] == []
    assert await _audit("linked_account.verify") == []


async def test_an_administrator_declares_a_link_that_is_not_verified(
    client: AsyncClient, admin: str, forge: FakeForge
) -> None:
    dev = await _member(client, "dev@varga.dev")
    other = await _member(client, "other@varga.dev")
    declared = await client.post(
        f"{ORG}/members/{dev}/linked-accounts",
        json={"provider": "gitlab", "external_id": "77", "login": "devon"},
    )
    assert declared.status_code == 201, declared.text
    assert declared.json()["verified"] is False and declared.json()["verified_at"] is None
    assert declared.json()["base_url"] == "https://gitlab.com" and declared.json()["created_by"] == admin
    forgejo = await client.post(
        f"{ORG}/members/{dev}/linked-accounts",
        json={
            "provider": "forgejo",
            "base_url": "https://Forge.Corp.example/",
            "external_id": "5",
            "login": "d",
        },
    )
    assert forgejo.status_code == 201 and forgejo.json()["base_url"] == "https://forge.corp.example"
    for refused in (
        {"provider": "forgejo", "external_id": "5", "login": "d"},  # Forgejo has no default instance
        {"provider": "github", "external_id": "devon", "login": "devon"},  # a login is never an id
        {"provider": "bitbucket", "external_id": "5", "login": "d"},
        {"provider": "gitlab", "base_url": "http://gitlab.corp.example", "external_id": "5", "login": "d"},
    ):
        assert (await client.post(f"{ORG}/members/{dev}/linked-accounts", json=refused)).status_code == 422

    await login(client, "dev@varga.dev")
    own = await client.get(f"{ORG}/members/{dev}/linked-accounts")
    assert own.status_code == 200 and {r["provider"] for r in own.json()} == {"gitlab", "forgejo"}
    assert all(r["verified"] is False for r in own.json())
    assert (await client.get(f"{ORG}/members/{other}/linked-accounts")).status_code == 403
    write = {"provider": "github", "external_id": "9", "login": "x"}
    assert (await client.post(f"{ORG}/members/{dev}/linked-accounts", json=write)).status_code == 403
    assert len(await _audit("linked_account.declare")) == 2


async def test_one_forge_account_belongs_to_one_member(
    client: AsyncClient, admin: str, forge: FakeForge
) -> None:
    dev = await _member(client, "dev@varga.dev")
    other = await _member(client, "other@varga.dev")
    await _configure(client, forge)
    forge.user("devon", 1001)
    declared = await client.post(
        f"{ORG}/members/{dev}/linked-accounts",
        json={"provider": "github", "external_id": "1001", "login": "devon"},
    )
    assert declared.status_code == 201
    taken = await client.post(
        f"{ORG}/members/{other}/linked-accounts",
        json={"provider": "github", "external_id": "1001", "login": "x"},
    )
    assert taken.status_code == 409, "a declaration never takes an account another member holds"

    # Another member proves the same account through the forge: refused, the declaration is not overruled.
    await login(client, "other@varga.dev")
    assert await _callback(client, forge.authorize(await _start(client), "devon")) == {
        "link_error": ["taken"]
    }
    assert (await _mine(client))["items"] == []

    # The member it was declared for proves it: the same row, now verified.
    await login(client, "dev@varga.dev")
    assert await _callback(client, forge.authorize(await _start(client), "devon")) == {"linked": ["github"]}
    [link] = (await _mine(client))["items"]
    assert link["id"] == declared.json()["id"] and link["verified"] is True

    # A later declaration of the same account does not downgrade the proof.
    await login(client, admin)
    again = await client.post(
        f"{ORG}/members/{dev}/linked-accounts",
        json={"provider": "github", "external_id": "1001", "login": "devon"},
    )
    assert again.status_code == 201 and again.json()["verified"] is True


async def test_a_member_unlinks_their_own_account_and_only_theirs(
    client: AsyncClient, admin: str, forge: FakeForge
) -> None:
    dev = await _member(client, "dev@varga.dev")
    other = await _member(client, "other@varga.dev")
    ids = {}
    for user_id, external in ((dev, "1"), (other, "2")):
        made = await client.post(
            f"{ORG}/members/{user_id}/linked-accounts",
            json={"provider": "github", "external_id": external, "login": f"u{external}"},
        )
        ids[user_id] = made.json()["id"]

    await login(client, "dev@varga.dev")
    assert (await client.delete(f"/api/v1/me/linked-accounts/{ids[other]}")).status_code == 404
    assert (await client.delete(f"/api/v1/me/linked-accounts/{ids[dev]}")).status_code == 204
    assert (await _mine(client))["items"] == []

    await login(client, admin)
    assert (await client.delete(f"{ORG}/members/{dev}/linked-accounts/{ids[other]}")).status_code == 404
    assert (await client.delete(f"{ORG}/members/{other}/linked-accounts/{ids[other]}")).status_code == 204
    assert len(await _audit("linked_account.delete")) == 2


async def test_gitlab_and_forgejo_instances_are_linked_the_same_way(
    client: AsyncClient, admin: str, forge: FakeForge
) -> None:
    await _member(client, "dev@varga.dev")
    await _configure(
        client, forge, name="gitlab-corp", provider="gitlab", base_url="https://gitlab.corp.example"
    )
    await _configure(client, forge, name="forge", provider="forgejo", base_url="https://forge.corp.example")
    forge.user("devon", 42)

    await login(client, "dev@varga.dev")
    for instance, provider in (("gitlab-corp", "gitlab"), ("forge", "forgejo")):
        url = await _start(client, instance, provider)
        assert await _callback(client, forge.authorize(url, "devon")) == {"linked": [provider]}
    links = {
        (r["provider"], r["base_url"], r["external_id"], r["verified"])
        for r in (await _mine(client))["items"]
    }
    assert links == {
        ("gitlab", "https://gitlab.corp.example", "42", True),
        ("forgejo", "https://forge.corp.example", "42", True),
    }
    paths = [r.url.path for r in forge.requests]
    assert paths == ["/oauth/token", "/api/v4/user", "/login/oauth/access_token", "/api/v1/user"]
    # The provider in the route must be the instance's.
    wrong = await client.post(
        "/api/v1/me/linked-accounts/github/start", params={"org": "varga", "instance": "gitlab-corp"}
    )
    assert wrong.status_code == 404
