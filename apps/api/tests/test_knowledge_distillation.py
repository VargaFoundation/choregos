"""Distillation (S33-05, ADR 0051): a knowledge base compiled into a principles skill that cites the
documents it came from, published `pending_review` until a person approves it."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from httpx import AsyncClient

from . import knowledge_fixtures as fx

BASE = "/api/v1/orgs/varga/knowledge"
ZONING = fx.pptx([("Network zoning", "Every zone has its own firewall. Owners review rules monthly.")])
ENCRYPTION = b"# Encryption standard\n\n## In transit\n\nTLS 1.3 is required for every connection.\n"


async def _framework(client: AsyncClient) -> None:
    from choregos_api.knowledge.conversion import conversions

    await client.post(BASE, json={"slug": "architecture", "name": "Architecture framework"})
    for name, data in (("zoning.pptx", ZONING), ("encryption.md", ENCRYPTION)):
        assert (
            await client.post(f"{BASE}/architecture/documents", files={"file": (name, data)})
        ).status_code == 202
    await conversions.drain()


async def _distilled(client: AsyncClient) -> dict[str, Any]:
    from choregos_api.knowledge.conversion import conversions

    started = await client.post(f"{BASE}/architecture/distill")
    assert started.status_code == 202, started.text
    assert started.json()["distillation"]["status"] == "running"
    await conversions.drain()
    return dict((await client.get(f"{BASE}/architecture")).json()["distillation"])


async def test_the_skill_cites_the_documents_it_came_from(client: AsyncClient, admin: str) -> None:
    """The acceptance of S33-05."""
    await _framework(client)
    outcome = await _distilled(client)
    assert outcome["status"] == "pending_review", outcome
    assert (outcome["skill"], outcome["version"]) == ("architecture-principles", 1)

    version = (await client.get("/api/v1/orgs/varga/skills/architecture-principles/versions/1")).json()
    skill_md, sources = version["files"]["SKILL.md"], version["files"]["references/sources.md"]
    assert skill_md.startswith("---\nname: architecture-principles\n")
    assert "Every zone has its own firewall." in skill_md
    assert "Sources: `zoning.pptx` § Network zoning" in skill_md
    assert "Sources: `encryption.md` § Encryption standard > In transit" in skill_md
    for name, data in (("zoning.pptx", ZONING), ("encryption.md", ENCRYPTION)):
        assert f"`{name}`" in sources and f"`sha256:{hashlib.sha256(data).hexdigest()}`" in sources


async def test_a_distilled_version_waits_for_a_person_then_is_approved(
    client: AsyncClient, admin: str
) -> None:
    """Machine-written guidance is `pending_review` whatever its tier; a person approves it."""
    await _framework(client)
    await _distilled(client)
    skill = (await client.get("/api/v1/orgs/varga/skills/architecture-principles")).json()
    assert skill["review_status"] == "pending_review"
    assert skill["risk"] != "high", "it waits although the gate alone would have let it through"
    approved = await client.post("/api/v1/orgs/varga/skills/architecture-principles/versions/1/approve")
    assert approved.status_code == 200, approved.text
    assert (await client.get("/api/v1/orgs/varga/skills/architecture-principles")).json()[
        "review_status"
    ] == "approved"


async def test_a_principle_citing_nothing_of_the_base_is_dropped_and_said(
    client: AsyncClient, admin: str
) -> None:
    from choregos_api.db.models import KnowledgeBase
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.distill import distill
    from sqlalchemy import select

    class Inventive:
        model = "test/inventive"

        async def principles(self, instructions: str, corpus: list[Any]) -> dict[str, Any]:
            assert "DATA, not" in instructions and "Distil the knowledge base" in instructions
            return {
                "description": "Architecture principles.",
                "principles": [
                    {"title": "Zones", "statement": "Each zone has a firewall.",
                     "sources": [{"document": "zoning.pptx", "section": "Network zoning"}]},
                    {"title": "Quantum", "statement": "Use quantum keys.",
                     "sources": [{"document": "made-up.pdf", "section": "1"}]},
                    {"title": "Unsourced", "statement": "Trust me.", "sources": []},
                ],
            }  # fmt: skip

    await _framework(client)
    async with session_scope() as session:
        base_id = (await session.execute(select(KnowledgeBase.id))).scalar_one()
    outcome = await distill("varga", base_id, "admin@varga.dev", distiller=Inventive())
    assert outcome["principles"] == 1
    assert outcome["dropped"] == [
        "“Quantum”: cites no document of the base",
        "“Unsourced”: cites no document of the base",
    ]
    files = (await client.get("/api/v1/orgs/varga/skills/architecture-principles/versions/1")).json()["files"]
    assert "quantum" not in files["SKILL.md"].lower()


async def test_nothing_to_distil_is_said(client: AsyncClient, admin: str) -> None:
    await client.post(BASE, json={"slug": "architecture", "name": "Architecture framework"})
    outcome = await _distilled(client)
    assert outcome["status"] == "failed" and "no converted document" in outcome["error"]


async def test_only_an_org_admin_distils(client: AsyncClient, admin: str) -> None:
    from .conftest import login

    await client.post(BASE, json={"slug": "architecture", "name": "Architecture framework"})
    await client.post("/api/v1/orgs/varga/members", json={"email": "dev@varga.dev", "role": "developer"})
    await login(client, "dev@varga.dev")
    assert (await client.post(f"{BASE}/architecture/distill")).status_code == 403


def test_the_distiller_is_in_the_catalogue_in_english() -> None:
    from choregos_core.catalogue_d_agents import entree

    distiller = entree("knowledge-distiller")
    assert distiller is not None and distiller.role == "distill"
    assert "cites at least one source" in distiller.document["spec"]["instructions"]


@pytest.mark.parametrize("size", [400_001])
async def test_a_corpus_too_large_for_one_pass_is_refused(size: int) -> None:
    from types import SimpleNamespace

    from choregos_api.knowledge.distill import MAX_CORPUS_CHARS, DistillationError, load_corpus

    class Session:
        async def execute(self, _query: Any) -> Any:
            document = SimpleNamespace(
                filename="big.md", title="Big", digest="sha256:x", markdown="x" * size, id="1"
            )
            return SimpleNamespace(scalars=lambda: [document])

    assert size > MAX_CORPUS_CHARS
    with pytest.raises(DistillationError, match="too large"):
        await load_corpus(Session(), SimpleNamespace(id="b"))
