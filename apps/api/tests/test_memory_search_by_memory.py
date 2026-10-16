"""Reviewing a project's other memories (S40-11a, ADR 0075): `?memory=`, `?agent=`, and the global routes."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_core.domain import Fact, Provenance
from httpx import AsyncClient

from .agent_scopes_kit import ORG, add_member, as_user, make_project

pytestmark = pytest.mark.usefixtures("real_adapters")


@pytest.fixture
def real_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_FAKES", "0")


def _memory() -> Any:
    from choregos_adapters import build

    return build("memory", "lexical", {"org": "varga"})


async def _setup(client: AsyncClient) -> str:
    project = await make_project(client, "billing-api")
    pid = str(project["id"])
    put = await client.put(f"/api/v1/projects/{pid}/connectors/memory", json={"type": "lexical"})
    assert put.status_code in {200, 201}, put.text
    made = await client.post(
        f"/api/v1/projects/{pid}/memories", json={"slug": "decisions", "display_name": "D"}
    )
    assert made.status_code == 201, made.text
    memory = _memory()
    await memory.write_fact(
        "billing-api", Fact(kind="decision", subject="vat", content="vat in the shared memory")
    )
    await memory.write_fact(
        "billing-api#agent:architect", Fact(kind="decision", subject="vat", content="vat in the own memory")
    )
    await memory.write_fact(
        "billing-api#memory:decisions",
        Fact(kind="decision", subject="vat", content="vat in the named memory"),
    )
    return pid


async def _contents(client: AsyncClient, pid: str, **params: str) -> list[str]:
    response = await client.get(f"/api/v1/projects/{pid}/memory/search", params={"q": "vat", **params})
    assert response.status_code == 200, response.text
    return [m["content"] for m in response.json()]


async def test_search_reads_the_memory_asked_and_defaults_to_the_shared_one(
    client: AsyncClient, admin: str
) -> None:
    pid = await _setup(client)
    assert await _contents(client, pid) == ["vat in the shared memory"], "unchanged without `memory`"
    assert await _contents(client, pid, memory="shared") == ["vat in the shared memory"]
    assert await _contents(client, pid, memory="decisions") == ["vat in the named memory"]
    assert await _contents(client, pid, memory="own", agent="architect") == ["vat in the own memory"]
    assert await _contents(client, pid, memory="own", agent="somebody-else") == []


async def test_bad_memory_parameters(client: AsyncClient, admin: str) -> None:
    pid = await _setup(client)
    unknown = await client.get(
        f"/api/v1/projects/{pid}/memory/search", params={"q": "vat", "memory": "nothing"}
    )
    assert unknown.status_code == 404
    no_agent = await client.get(f"/api/v1/projects/{pid}/memory/search", params={"q": "vat", "memory": "own"})
    assert no_agent.status_code == 422


async def test_pending_review_of_an_agents_own_and_a_named_memory(client: AsyncClient, admin: str) -> None:
    pid = await _setup(client)
    memory = _memory()
    fact = Fact(kind="run_lesson", subject="fx", content="Convert at the daily rate.")
    own = await memory.propose_fact(
        "billing-api#agent:architect", fact, Provenance(source="agent", run_id="r1")
    )
    named = await memory.propose_fact(
        "billing-api#memory:decisions", fact, Provenance(source="agent", run_id="r2")
    )
    url = f"/api/v1/projects/{pid}/memory/pending"
    assert (await client.get(url)).json() == [], "the shared memory has nothing pending"
    assert [
        m["id"] for m in (await client.get(url, params={"memory": "own", "agent": "architect"})).json()
    ] == [own]
    assert [m["id"] for m in (await client.get(url, params={"memory": "decisions"})).json()] == [named]
    # A decision is scoped to the namespace it is made in.
    wrong = await client.post(url, json={"id": own, "action": "accept"}, params={"memory": "decisions"})
    assert wrong.json() == {"status": "not_found"}
    right = await client.post(
        url, json={"id": own, "action": "accept"}, params={"memory": "own", "agent": "architect"}
    )
    assert right.json() == {"status": "accepted"}


async def test_global_memory_search_pending_and_decision(client: AsyncClient, admin: str) -> None:
    await _setup(client)
    await client.post(f"{ORG}/memories", json={"slug": "lessons", "display_name": "L"})
    # The organisation declares where its global memories live (else the deployment default, Ecphoria).
    declared = await client.post(
        f"{ORG}/connectors", json={"name": "memory-org", "type": "lexical", "kind": "memory"}
    )
    assert declared.status_code == 201, declared.text
    ns = "org:varga#memory:lessons"
    memory = _memory()
    await memory.write_fact(ns, Fact(kind="decision", subject="tls", content="Pin TLS 1.2 everywhere."))
    proposed = await memory.propose_fact(
        ns,
        Fact(kind="run_lesson", subject="hsts", content="Send HSTS."),
        Provenance(source="agent", run_id="r1"),
    )
    found = await client.get(f"{ORG}/memories/lessons/search", params={"q": "TLS"})
    assert [m["subject"] for m in found.json()] == ["tls"]
    pending = await client.get(f"{ORG}/memories/lessons/pending")
    assert [m["id"] for m in pending.json()] == [proposed]
    decided = await client.post(f"{ORG}/memories/lessons/pending", json={"id": proposed, "action": "accept"})
    assert decided.json() == {"status": "accepted"}
    assert [
        m["subject"]
        for m in (await client.get(f"{ORG}/memories/lessons/search", params={"q": "HSTS"})).json()
    ] == ["hsts"]
    # Only an administrator decides.
    await add_member(client, "dev@varga.dev", "developer")
    await as_user(client, "dev@varga.dev")
    assert (await client.get(f"{ORG}/memories/lessons/pending")).status_code == 403
    refused = await client.post(f"{ORG}/memories/lessons/pending", json={"id": proposed, "action": "reject"})
    assert refused.status_code == 403
    assert (await client.get(f"{ORG}/memories/lessons/search", params={"q": "TLS"})).status_code == 200
