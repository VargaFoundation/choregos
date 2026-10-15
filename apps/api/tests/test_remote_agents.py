"""Remote agents in the registry (ADR 0062): the card is read at publication, checked and pinned."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from choregos_adapters.a2a import parse_card
from choregos_adapters.fakes.a2a import FakeA2aAgent
from httpx import AsyncClient

from .conftest import login
from .conftest_remote import remote_agent, set_sealing_key

AGENTS = "/api/v1/orgs/varga/agents"
CREDENTIALS = "/api/v1/orgs/varga/remote-credentials"


@pytest.fixture(autouse=True)
def _sealed(monkeypatch: pytest.MonkeyPatch) -> None:
    set_sealing_key(monkeypatch)


@pytest.fixture
def remote() -> Iterator[FakeA2aAgent]:
    yield from remote_agent()


def _runtime(remote: FakeA2aAgent, **extra: Any) -> dict[str, Any]:
    return {
        "kind": "a2a",
        "card_url": remote.card_url,
        "skill": "work-in-jira",
        "credential": "rovo",
        **extra,
    }


def _body(
    remote: FakeA2aAgent, *, kind: str = "remote", spec: dict[str, Any] | None = None, **runtime: Any
) -> Any:
    return {
        "slug": "rovo",
        "kind": kind,
        "display_name": "Rovo",
        "spec": spec
        if spec is not None
        else {"instructions": "Be brief.", "runtime": _runtime(remote, **runtime)},
    }


async def _credential(client: AsyncClient, kind: str = "bearer") -> None:
    if kind == "bearer":
        await client.post(CREDENTIALS, json={"name": "rovo", "type": "bearer", "token": "a-static-token"})
    else:
        await client.post(CREDENTIALS, json={"name": "rovo", "type": "none"})


async def test_publication_reads_the_card_and_pins_its_digest(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    remote.security = "bearer"
    await _credential(client)
    created = await client.post(AGENTS, json=_body(remote))
    assert created.status_code == 201, created.text
    assert created.json()["kind"] == "remote"
    version = (await client.get(f"{AGENTS}/rovo/versions/1")).json()
    pinned = version["spec"]["runtime"]["pinned_card"]
    assert pinned["digest"] == parse_card(remote.card_document()).digest
    assert pinned["url"] == remote.endpoint and pinned["protocol_version"] == "0.3.0"
    assert pinned["skills"] == ["work-in-jira", "work-in-confluence"]
    assert remote.card_fetches >= 1


async def test_a_pinned_card_the_caller_wrote_is_replaced_by_the_one_that_was_read(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    remote.security = "bearer"
    await _credential(client)
    forged = {
        "url": "https://evil.example/rpc",
        "protocol_version": "0.3.0",
        "digest": "sha256:" + "a" * 64,
        "skills": [],
    }
    created = await client.post(AGENTS, json=_body(remote, pinned_card=forged))
    pinned = (await client.get(f"{AGENTS}/rovo/versions/1")).json()["spec"]["runtime"]["pinned_card"]
    assert created.status_code == 201 and pinned["url"] == remote.endpoint


async def test_a_new_version_pins_the_card_as_it_is_now(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    remote.security = "bearer"
    await _credential(client)
    await client.post(AGENTS, json=_body(remote))
    remote.skills = ["work-in-jira"]
    published = await client.post(
        f"{AGENTS}/rovo/versions", json={"instructions": "v2", "runtime": _runtime(remote)}
    )
    assert published.status_code == 201, published.text
    v1 = (await client.get(f"{AGENTS}/rovo/versions/1")).json()["spec"]["runtime"]["pinned_card"]
    v2 = (await client.get(f"{AGENTS}/rovo/versions/2")).json()["spec"]["runtime"]["pinned_card"]
    assert v1["digest"] != v2["digest"] and v2["skills"] == ["work-in-jira"]


async def test_a_remote_agent_without_an_a2a_runtime_is_refused(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    refused = await client.post(AGENTS, json=_body(remote, spec={"instructions": "x"}))
    assert refused.status_code == 422 and "a2a" in refused.text


async def test_an_a2a_runtime_on_an_internal_agent_is_refused(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    remote.security = "none"  # everything else is valid: only the kind is wrong
    refused = await client.post(AGENTS, json=_body(remote, kind="internal", credential=None))
    assert refused.status_code == 422 and "remote" in refused.text
    internal = await client.post(
        AGENTS, json={"slug": "plain", "display_name": "P", "spec": {"instructions": "x"}}
    )
    assert internal.status_code == 201
    on_version = await client.post(
        f"{AGENTS}/plain/versions", json={"instructions": "x", "runtime": _runtime(remote, credential=None)}
    )
    assert on_version.status_code == 422, "the kind of an existing agent does not change by publishing"


async def test_an_unknown_skill_is_refused_naming_the_cards(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    remote.security = "bearer"
    await _credential(client)
    refused = await client.post(AGENTS, json=_body(remote, skill="work-in-bitbucket"))
    assert refused.status_code == 422
    assert "work-in-bitbucket" in refused.text and "work-in-jira" in refused.text


async def test_a_credential_of_a_type_the_card_does_not_accept_is_refused(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    # the card asks for OAuth; the organisation only holds a bearer token
    await _credential(client)
    refused = await client.post(AGENTS, json=_body(remote))
    assert refused.status_code == 422 and "oauth2_authorization_code" in refused.text
    assert (await client.get(f"{AGENTS}/rovo")).status_code == 404


async def test_a_credential_that_does_not_exist_or_is_missing_is_refused(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    missing = await client.post(AGENTS, json=_body(remote))
    assert missing.status_code == 422 and "no remote credential" in missing.text
    unnamed = await client.post(AGENTS, json=_body(remote, credential=None))
    assert unnamed.status_code == 422 and "requires authentication" in unnamed.text


async def test_a_card_that_asks_for_no_authentication_needs_no_credential(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    remote.security = "none"
    created = await client.post(AGENTS, json=_body(remote, credential=None))
    assert created.status_code == 201, created.text


async def test_a_card_that_cannot_be_read_blocks_the_publication(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    remote.host = "http://agent.remote.test"  # plain http: refused before anything is fetched
    refused = await client.post(AGENTS, json=_body(remote, card_url="https://agent.remote.test/missing.json"))
    assert refused.status_code == 422 and "agent card" in refused.text
    assert (
        await client.post(AGENTS, json=_body(remote, card_url="http://agent.remote.test/card"))
    ).status_code == 422


async def test_a_remote_agent_brings_its_own_model_and_none_of_the_platforms_tools(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    remote.security = "none"
    for extra in ({"model": "profile:standard"}, {"backend": "codex"}, {"skills": [{"slug": "reviewer"}]}):
        spec = {"instructions": "x", "runtime": _runtime(remote, credential=None), **extra}
        refused = await client.post(AGENTS, json=_body(remote, spec=spec))
        assert refused.status_code == 422, extra


async def test_only_an_org_admin_creates_a_remote_agent(
    client: AsyncClient, admin: str, remote: FakeA2aAgent, project: dict[str, Any]
) -> None:
    remote.security = "none"
    await client.post(
        "/api/v1/orgs/varga/members",
        json={"email": "dev@varga.dev", "role": "project_owner", "project_slug": "billing-api"},
    )
    client.cookies.clear()
    await login(client, "dev@varga.dev")
    refused = await client.post(AGENTS, json=_body(remote, credential=None))
    assert refused.status_code == 403
    assert (await client.post(f"{AGENTS}/discover", json={"card_url": remote.card_url})).status_code == 403


async def test_discovery_summarises_the_card_and_proposes_an_entry_without_creating_it(
    client: AsyncClient, admin: str, remote: FakeA2aAgent
) -> None:
    await client.post(
        CREDENTIALS,
        json={"name": "rovo-oauth", "type": "oauth2_authorization_code", "card_url": remote.card_url},
    )
    await client.post(CREDENTIALS, json={"name": "static", "type": "bearer", "token": "t-value"})
    found = await client.post(f"{AGENTS}/discover", json={"card_url": remote.card_url})
    assert found.status_code == 200, found.text
    body = found.json()
    assert body["card"]["digest"] == parse_card(remote.card_document()).digest
    assert [s["id"] for s in body["card"]["skills"]] == ["work-in-jira", "work-in-confluence"]
    assert body["card"]["auth_types"] == ["oauth2_authorization_code"] and body["card"]["dcr_available"]
    assert body["credentials"] == ["rovo-oauth"], "only the credentials of a type the card accepts"
    assert body["domains"] == ["agent.remote.test"]
    proposed = body["proposed"]
    assert proposed["kind"] == "remote" and proposed["lifecycle"] == "draft"
    assert proposed["spec"]["runtime"]["credential"] == "rovo-oauth"
    assert (await client.get(AGENTS)).json() == [], "nothing was created"
