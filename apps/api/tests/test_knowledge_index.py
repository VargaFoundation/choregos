"""Serving mode `index` (S33-03, ADR 0051): contextual chunks, hybrid search, citations, recall@5."""

from __future__ import annotations

import os
from typing import Any

import pytest
from httpx import AsyncClient

from . import knowledge_fixtures as fx
from .conftest import SUITE_SUR_POSTGRES, sans_postgres

BASE = "/api/v1/orgs/varga/knowledge"
#: How the index ranks on the database the suite runs on: in the process on SQLite; on PostgreSQL,
#: hybrid when pgvector is there (required where the image carries it), full-text otherwise.
MODES = (
    {"in-process"}
    if not SUITE_SUR_POSTGRES
    else {"hybrid"}
    if os.environ.get("CHOREGOS_TEST_EXPECT_PGVECTOR") == "1"
    else {"hybrid", "full-text"}
)

#: A small framework, one document per format, and the eval set that goes with it.
CORPUS: list[tuple[str, bytes]] = []
EVAL_SET = [
    {"question": "Which TLS version is required for data in transit?", "expected": ["encryption.docx"]},
    {"question": "Does every network zone need its own firewall?", "expected": ["zoning.pptx"]},
    {"question": "How long are application logs retained?", "expected": ["retention.pdf"]},
    {"question": "Who owns the backup of the billing database?", "expected": ["owners.xlsx"]},
    {"question": "What do we do when a certificate expires in production?", "expected": ["runbook.md"]},
]


def corpus() -> list[tuple[str, bytes]]:
    if not CORPUS:
        CORPUS.extend(
            [
                (
                    "encryption.docx",
                    fx.docx(
                        [
                            (1, "Encryption standard", ""),
                            (
                                2,
                                "In transit",
                                "TLS 1.3 is required for every connection; TLS 1.2 is refused.",
                            ),
                            (2, "At rest", "Disks are encrypted with keys held in the vault."),
                        ]
                    ),
                ),
                (
                    "zoning.pptx",
                    fx.pptx(
                        [
                            (
                                "Network zoning principles",
                                "Every zone has its own firewall and a single owner",
                            ),
                            ("DMZ", "Only reverse proxies live in the DMZ"),
                        ]
                    ),
                ),
                ("retention.pdf", fx.pdf(["Log retention policy", "Application logs are retained 90 days"])),
                (
                    "owners.xlsx",
                    fx.xlsx({"Owners": [["system", "backup owner"], ["billing database", "dba team"]]}),
                ),
                (
                    "runbook.md",
                    b"# Incident runbook\n\n## Certificate expired in production\n\n"
                    b"Renew it with the ACME client, then restart the ingress.\n",
                ),
            ]
        )
    return CORPUS


async def _indexed_base(client: AsyncClient, slug: str = "framework", **fields: Any) -> dict[str, Any]:
    from choregos_api.knowledge.conversion import conversions

    created = await client.post(
        BASE, json={"slug": slug, "name": "Framework", "serving_mode": "index", **fields}
    )
    assert created.status_code == 201, created.text
    for name, data in corpus():
        sent = await client.post(f"{BASE}/{slug}/documents", files={"file": (name, data)})
        assert sent.status_code == 202, sent.text
    await conversions.drain()
    return dict((await client.get(f"{BASE}/{slug}")).json())


# ───────────────────────────── units ─────────────────────────────


def test_a_chunk_carries_its_document_title_and_heading_path() -> None:
    from choregos_api.knowledge.chunking import chunk

    markdown = (
        "<!-- Slide number: 1 -->\n# Encryption standard\n\nintro\n\n## In transit\n\nTLS 1.3.\n\n"
        "### Exceptions\n\nnone\n\n## At rest\n\n"
        + "\n\n".join(f"paragraph {i} " + "x" * 300 for i in range(6))
    )
    chunks = chunk(markdown, "Encryption standard")
    paths = [c.heading_path for c in chunks]
    assert paths[:3] == [
        ["Encryption standard"],
        ["Encryption standard", "In transit"],
        ["Encryption standard", "In transit", "Exceptions"],
    ]
    assert chunks[1].text == "Encryption standard > In transit\n\nTLS 1.3."
    at_rest = [c for c in chunks if c.heading_path[-1] == "At rest"]
    assert len(at_rest) > 1 and all(len(c.body) <= 1200 for c in at_rest), "a long section is cut"
    assert not any("Slide number" in c.text for c in chunks)


def test_reciprocal_rank_fusion_rewards_agreement() -> None:
    from choregos_api.knowledge.index import bm25, fuse

    assert [key for key, _ in fuse(["a", "b", "c"], ["b", "c", "d"])][:2] == ["b", "c"]
    ranked = bm25("tls version", {"1": "TLS 1.3 everywhere, tls only", "2": "disks at rest", "3": "tls"})
    assert ranked[0] in {"1", "3"} and "2" not in ranked


async def test_the_fake_embedder_is_deterministic() -> None:
    from choregos_api.knowledge.embeddings import FakeEmbedder, cosine

    one, two, other = await FakeEmbedder().embed(["TLS 1.3 in transit", "TLS 1.3 in transit", "backup owner"])
    assert one == two
    assert cosine(one, two) == pytest.approx(1.0) and cosine(one, other) < 0.5


# ───────────────────────────── the API ─────────────────────────────


async def test_a_search_answers_citations(client: AsyncClient, admin: str) -> None:
    base = await _indexed_base(client)
    assert base["index"]["documents"] == 5, base
    found = await client.post(f"{BASE}/framework/search", json={"query": "Which TLS version in transit?"})
    assert found.status_code == 200, found.text
    result = found.json()
    assert result["mode"] in MODES, "the answer says how it ranked"
    top = result["hits"][0]
    assert (top["filename"], top["heading_path"]) == (
        "encryption.docx",
        ["Encryption standard", "In transit"],
    )
    assert "TLS 1.3 is required" in top["excerpt"]
    assert top["document"] == "Encryption standard" and top["knowledge_base"] == "framework"


async def test_recall_at_5_on_the_eval_set_is_reported(client: AsyncClient, admin: str) -> None:
    """The acceptance of S33-03."""
    await _indexed_base(client)
    put = await client.put(f"{BASE}/framework/eval", json={"cases": EVAL_SET})
    assert put.status_code == 200, put.text
    ran = await client.post(f"{BASE}/framework/eval/run")
    assert ran.status_code == 200, ran.text
    result = ran.json()["eval_result"]
    assert result["k"] == 5 and result["mode"] in MODES
    assert result["recall"] == 1.0, result["cases"]
    assert (await client.get(f"{BASE}/framework")).json()["eval_result"]["recall"] == 1.0, "kept for the page"


async def test_a_files_base_switched_to_index_is_indexed(client: AsyncClient, admin: str) -> None:
    from choregos_api.knowledge.conversion import conversions

    await _indexed_base(client, "framework", serving_mode="files")
    assert (await client.get(f"{BASE}/framework")).json()["index"] is None
    assert (await client.post(f"{BASE}/framework/search", json={"query": "TLS"})).status_code == 409
    await client.patch(f"{BASE}/framework", json={"serving_mode": "index"})
    await conversions.drain()
    assert (await client.get(f"{BASE}/framework")).json()["index"]["documents"] == 5
    again = await client.post(f"{BASE}/framework/reindex")
    assert again.status_code == 202
    await conversions.drain()
    assert (await client.get(f"{BASE}/framework")).json()["index"]["documents"] == 5


async def test_a_deleted_document_leaves_the_index(client: AsyncClient, admin: str) -> None:
    base = await _indexed_base(client)
    encryption = next(d for d in base["documents"] if d["filename"] == "encryption.docx")
    await client.delete(f"{BASE}/framework/documents/{encryption['id']}")
    found = (await client.post(f"{BASE}/framework/search", json={"query": "TLS 1.3 in transit"})).json()
    assert "encryption.docx" not in {hit["filename"] for hit in found["hits"]}


async def test_a_run_searches_only_the_bases_it_is_served(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    """The sidecar's `knowledge_search`: the run's `index` bases, while served to its project."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    await _indexed_base(client, "framework")
    await _indexed_base(client, "other-team", scope="projects", projects=["billing-api"])
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="BILLING-9", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="run-search",
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="implement",
                status="running",
                stage_input={"knowledge": [{"slug": "framework", "mode": "index"}]},
            )
        )
    token = mint_run_token(
        "run-search", project_slug="billing-api", work_item_key="BILLING-9", ttl_minutes=30
    )
    headers = {"Authorization": f"Bearer {token}"}

    async def bases_found(query: str) -> set[str]:
        answer = await client.post(
            "/api/v1/internal/runs/run-search/knowledge/search", json={"query": query}, headers=headers
        )
        assert answer.status_code == 200, answer.text
        return {hit["knowledge_base"] for hit in answer.json()["hits"]}

    assert await bases_found("TLS version in transit") == {"framework"}, (
        "not `other-team`: the run does not name it"
    )
    await client.patch(f"{BASE}/framework", json={"scope": "projects", "projects": ["billing-api"]})
    assert await bases_found("TLS") == {"framework"}
    await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": "web", "name": "Web", "config": {"slug": "web", "org": "varga"}},
    )
    await client.patch(f"{BASE}/framework", json={"projects": ["web"]})
    assert await bases_found("TLS") == set(), "a base no longer served to the project answers nothing"


# ───────────────────────────── PostgreSQL ─────────────────────────────


@sans_postgres
async def test_postgres_ranks_hybrid_with_pgvector_and_full_text_without(pg_app: Any) -> None:
    """With pgvector the mode is `hybrid`; without it (the migration could not create the extension),
    `full-text` — and both find the right passage. Which one runs depends on the database given."""
    from choregos_api.db.models import KnowledgeBase, KnowledgeDocument, Organization
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.index import has_vector_column, index_document, search
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        org = Organization(slug="a", name="A")
        session.add(org)
        await session.flush()
        base = KnowledgeBase(
            org_id=org.id, slug="kb", name="KB", scope="org", projects=[], groups=[], serving_mode="index"
        )
        session.add(base)
        await session.flush()
        ids = []
        for name, markdown in (
            ("encryption.docx", "# Encryption standard\n\n## In transit\n\nTLS 1.3 is required.\n"),
            ("zoning.pptx", "# Network zoning\n\nEvery zone has a firewall.\n"),
        ):
            document = KnowledgeDocument(
                org_id=org.id, knowledge_base_id=base.id, filename=name, title=name, mime="x",
                size_bytes=1, digest=f"sha256:{name}", markdown=markdown, status="ready",
            )  # fmt: skip
            session.add(document)
            await session.flush()
            ids.append(document.id)
    for document_id in ids:
        assert await index_document("a", document_id) > 0
    async with session_scope(orgs=["a"]) as session:
        vector = await has_vector_column(session)
        base = (await session.execute(select(KnowledgeBase))).scalar_one()
        result = await search(session, [base], "which TLS version in transit")
    assert result.mode == ("hybrid" if vector else "full-text")
    if os.environ.get("CHOREGOS_TEST_EXPECT_PGVECTOR") == "1":
        assert vector, "the database has pgvector: the hybrid ranking must be what ran"
    assert result.hits[0].filename == "encryption.docx"
    assert result.hits[0].heading_path == ["Encryption standard", "In transit"]
    async with session_scope(orgs=["b"]) as session:
        assert (await search(session, [base], "TLS")).hits == [], (
            "another organisation's chunks are invisible"
        )
