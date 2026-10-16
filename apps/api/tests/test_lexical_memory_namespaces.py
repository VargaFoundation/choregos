"""The lexical memory serves the four namespaces without mixing them (S40-11a, ADR 0075)."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_core.domain import Fact, Provenance
from httpx import AsyncClient

from .agent_scopes_kit import ORG, make_project

pytestmark = pytest.mark.usefixtures("real_adapters")

SUBJECT = "rounding"


@pytest.fixture
def real_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    """`CHOREGOS_FAKES=1` would swap the lexical memory for the in-memory fake: we want the database."""
    monkeypatch.setenv("CHOREGOS_FAKES", "0")


def _memory() -> Any:
    from choregos_adapters import build

    return build("memory", "lexical", {"org": "varga"})


async def _setup(client: AsyncClient) -> Any:
    project = await make_project(client, "billing-api")
    await client.post(
        f"/api/v1/projects/{project['id']}/memories", json={"slug": "decisions", "display_name": "D"}
    )
    await client.post(f"{ORG}/memories", json={"slug": "lessons", "display_name": "L"})
    return project


def _fact(content: str) -> Fact:
    return Fact(kind="decision", subject=SUBJECT, content=content)


async def test_the_four_namespaces_do_not_mix(client: AsyncClient, admin: str) -> None:
    await _setup(client)
    memory = _memory()
    namespaces = {
        "shared": "billing-api",
        "own": "billing-api#agent:architect",
        "named": "billing-api#memory:decisions",
        "global": "org:varga#memory:lessons",
    }
    for name, namespace in namespaces.items():
        await memory.write_fact(namespace, _fact(f"rounding rule of the {name} memory"))
    for name, namespace in namespaces.items():
        found = await memory.search(namespace, "rounding rule", k=10)
        assert [m.content for m in found] == [f"rounding rule of the {name} memory"], name
    # Supersession is per namespace: a second write to one leaves the others' fact active.
    await memory.write_fact(namespaces["named"], _fact("rounding rule, rewritten"))
    assert [m.content for m in await memory.search(namespaces["named"], "rounding", k=10)] == [
        "rounding rule, rewritten"
    ]
    assert len(await memory.search(namespaces["shared"], "rounding", k=10)) == 1
    pack = await memory.context_pack(namespaces["global"], "rounding", [], budget_tokens=500)
    assert [m.content for m in pack.memories] == ["rounding rule of the global memory"]


async def test_facts_carry_their_organisation_and_store(client: AsyncClient, admin: str) -> None:
    from choregos_api.db.models import MemoryFact, MemoryStore, Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    project = await _setup(client)
    memory = _memory()
    await memory.write_fact("billing-api", _fact("shared one"))
    await memory.write_fact("org:varga#memory:lessons", _fact("global one"))
    async with session_scope() as session:
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "varga"))
        ).scalar_one()
        store_id = (
            await session.execute(select(MemoryStore.id).where(MemoryStore.slug == "lessons"))
        ).scalar_one()
        rows = {row.content: row for row in (await session.execute(select(MemoryFact))).scalars()}
    shared, global_ = rows["shared one"], rows["global one"]
    assert (shared.org_id, shared.project_id, shared.memory_store_id) == (org_id, project["id"], None)
    assert (global_.org_id, global_.project_id, global_.memory_store_id) == (org_id, None, store_id)


async def test_a_proposal_to_a_global_memory_is_pending_and_decided_only_in_its_namespace(
    client: AsyncClient, admin: str
) -> None:
    await _setup(client)
    memory = _memory()
    global_ns = "org:varga#memory:lessons"
    proposed = await memory.propose_fact(
        global_ns,
        Fact(kind="run_lesson", subject="tls", content="Pin TLS 1.2."),
        Provenance(source="agent", run_id="r1"),
    )
    assert await memory.search(global_ns, "tls", k=5) == [], "a proposal is not a fact yet"
    assert [m.id for m in await memory.list_pending(global_ns)] == [proposed]
    # The project's shared namespace cannot decide it: the fact is not in that namespace.
    assert await memory.accept_pending("billing-api", proposed) is False
    assert [m.id for m in await memory.list_pending(global_ns)] == [proposed]
    assert await memory.accept_pending(global_ns, proposed) is True
    assert [m.subject for m in await memory.search(global_ns, "tls", k=5)] == ["tls"]


async def test_an_unknown_store_raises_instead_of_reading_the_shared_memory(
    client: AsyncClient, admin: str
) -> None:
    await _setup(client)
    memory = _memory()
    with pytest.raises(ValueError, match="unknown project memory"):
        await memory.search("billing-api#memory:nothing", "x", k=1)
    with pytest.raises(ValueError, match="unknown global memory"):
        await memory.search("org:varga#memory:nothing", "x", k=1)
