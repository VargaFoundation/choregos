"""The lexical memory (the fallback with no extra service) against a real database.

It had never been exercised on a database: `lexical_vector` and `similarity` had their tests, the
adapter had none. On PostgreSQL, it saw NOTHING, as it happens: the factory gave it a bare
session, and the fail-closed RLS shows nothing to a session that does not name its organisation.
SQLite (without RLS) let it through.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_core.domain import Fact, Provenance
from httpx import ASGITransport, AsyncClient

from .conftest import login, without_postgres


@pytest.fixture(autouse=True)
def real_adapters(monkeypatch: pytest.MonkeyPatch) -> None:
    """`CHOREGOS_FAKES=1` would replace the fallback with the in-memory fake: here we want the database."""
    monkeypatch.setenv("CHOREGOS_FAKES", "0")


async def _project(client: AsyncClient, org: str, slug: str) -> str:
    payload = {"slug": slug, "name": slug, "config": {"slug": slug, "org": org}}
    response = await client.post(f"/api/v1/orgs/{org}/projects", json=payload)
    assert response.status_code == 201, response.text
    project_id = str(response.json()["id"])
    upsert = await client.put(f"/api/v1/projects/{project_id}/connectors/memory", json={"type": "pgvector"})
    assert upsert.status_code in {200, 201}, upsert.text
    return project_id


def _memory(org: str) -> Any:
    from choregos_adapters import build

    return build("memory", "pgvector", {"org": org})


async def test_write_search_propose_and_validate_on_the_database(client: AsyncClient, org: str) -> None:
    """The full cycle, on SQLite: governed write, lexical search, an agent's proposal,
    human validation through the API, which reads the project's memory connector."""
    await login(client, "admin@varga.dev")
    project_id = await _project(client, org, "billing")
    memory = _memory(org)

    first = await memory.write_fact(
        "billing",
        Fact(kind="decision", subject="rounding", content="Amounts are rounded to the cent."),
    )
    second = await memory.write_fact(
        "billing",
        Fact(kind="decision", subject="rounding", content="Amounts are rounded to the half cent."),
    )
    assert first != second
    found = await memory.search("billing", "rounding of amounts", k=5)
    assert [m.id for m in found] == [second], "the previous fact of the same subject is superseded"

    proposed = await memory.propose_fact(
        "billing",
        Fact(kind="run_lesson", subject="vat", content="VAT is computed after the discount."),
        Provenance(source="agent", run_id="run-1"),
    )
    via_api = await client.get(f"/api/v1/projects/{project_id}/memory/pending")
    assert via_api.status_code == 200, via_api.text
    assert [m["id"] for m in via_api.json()] == [proposed]
    assert via_api.json()[0]["proposed_by"] == "run-1"

    decision = await client.post(
        f"/api/v1/projects/{project_id}/memory/pending", json={"id": proposed, "action": "accept"}
    )
    assert decision.status_code == 200, decision.text
    assert decision.json() == {"status": "accepted"}

    search = await client.get(f"/api/v1/projects/{project_id}/memory/search", params={"q": "VAT discount"})
    assert search.status_code == 200, search.text
    assert [m["subject"] for m in search.json()] == ["vat"]

    pack = await memory.context_pack("billing", "vat", paths=[], budget_tokens=500)
    assert [m.subject for m in pack.memories] == ["vat"]
    assert pack.tokens_estimated > 0


async def test_without_an_organisation_the_factory_refuses(app: Any) -> None:
    from choregos_adapters import build
    from choregos_adapters.errors import ConfigurationError

    with pytest.raises(ConfigurationError, match="org"):
        build("memory", "pgvector", {})


@without_postgres
async def test_on_postgres_each_organisation_only_sees_its_own_memory(pg_app: Any) -> None:
    """Two organisations, one `billing-api` project each. `a`'s adapter reads and writes at `a`;
    `b`'s does not see `a`'s facts; a bare session, the former wiring, does not even see the project."""
    from choregos_adapters.memory.lexical import LexicalMemory, MemoryModels
    from choregos_api.db.models import MemoryFact, Organization, Project
    from choregos_api.db.session import get_sessionmaker, session_scope
    from choregos_api.services import ensure_defaults

    async with session_scope(orgs="*") as session:
        for slug in ("a", "b"):
            organisation = Organization(slug=slug, name=slug.upper())
            session.add(organisation)
            await session.flush()
            project = Project(
                org_id=organisation.id,
                slug="billing-api",
                name="Billing",
                status="active",
                config={"slug": "billing-api", "org": slug},
            )
            session.add(project)
            await session.flush()
            await ensure_defaults(session, project)

    at_a = _memory("a")
    at_b = _memory("b")
    await at_a.write_fact(
        "billing-api", Fact(kind="decision", subject="currency", content="Everything is in euros.")
    )

    assert [m.subject for m in await at_a.search("billing-api", "euros", k=5)] == ["currency"]
    assert await at_b.search("billing-api", "euros", k=5) == []

    bare = LexicalMemory(get_sessionmaker(), MemoryModels(Project=Project, MemoryFact=MemoryFact))
    with pytest.raises(ValueError, match="projet inconnu"):  # lang: fr-ok (pinned by the adapter's message)
        await bare.search("billing-api", "euros", k=5)

    # And through the API: a developer of `a` searches in THEIR project and finds the fact.
    async with AsyncClient(transport=ASGITransport(app=pg_app), base_url="http://test") as client:
        await login(client, "admin@a.test")
        upsert = await client.put(
            "/api/v1/projects/a:billing-api/connectors/memory", json={"type": "pgvector"}
        )
        assert upsert.status_code in {200, 201}, upsert.text
        search = await client.get("/api/v1/projects/a:billing-api/memory/search", params={"q": "euros"})
        assert search.status_code == 200, search.text
        assert [m["subject"] for m in search.json()] == ["currency"]


def test_both_names_build_the_same_fallback() -> None:
    """`pgvector` is a deprecated alias of `lexical`, and it must stay one.

    The name promised vector search: there is no `vector` extension in the migrations, the
    `embedding` column is a `Json`, and the similarity is a dot product between bags of words
    computed in Python. Renaming was honest; breaking the projects that already wrote `pgvector`
    in their connector would not have been.
    """
    import os

    from choregos_adapters import build
    from choregos_adapters.memory.lexical import LexicalMemory

    os.environ["CHOREGOS_FAKES"] = "0"
    try:
        for name in ("lexical", "pgvector"):
            assert isinstance(build("memory", name, {"org": "*"}), LexicalMemory), name
    finally:
        os.environ["CHOREGOS_FAKES"] = "1"


async def test_the_interface_catalogue_no_longer_offers_the_old_name(client: AsyncClient) -> None:
    """A new project must not be able to choose a name that lies."""
    types = (await client.get("/api/v1/connectors/types")).json()
    memory = {entry["type"] for entry in types if entry["kind"] == "memory"}
    assert "lexical" in memory
    assert "pgvector" not in memory, "the old name is still offered at creation"


async def test_an_agents_own_memory_is_kept_apart_from_the_projects(client: AsyncClient, org: str) -> None:
    """S32-02: `<project>#agent:<slug>` is the agent's own memory in that project. The project does
    not read it, the agent reads only it, and a fact of the same subject supersedes nothing across."""
    from choregos_core import memory_namespace

    await login(client, "admin@varga.dev")
    await _project(client, org, "billing")
    memory = _memory(org)
    agent = memory_namespace("billing", "agent", "accountant")
    await memory.write_fact(
        "billing", Fact(kind="decision", subject="rounding", content="Round to the cent.")
    )
    await memory.write_fact(
        agent, Fact(kind="decision", subject="rounding", content="Round to the half cent.")
    )
    project_facts = [m.content for m in await memory.search("billing", "rounding", k=5)]
    own = [m.content for m in await memory.search(agent, "rounding", k=5)]
    assert (project_facts, own) == (["Round to the cent."], ["Round to the half cent."])
    await memory.propose_fact(
        agent, Fact(kind="decision", subject="vat", content="VAT at 20 %."), Provenance(source="agent")
    )
    assert [m.content for m in await memory.list_pending(agent)] == ["VAT at 20 %."]
    assert await memory.list_pending("billing") == []
