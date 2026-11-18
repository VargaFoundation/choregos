"""Serving mode `index` (S33-03, ADR 0051): contextual chunks, hybrid search, citations, recall@5."""

from __future__ import annotations

import os
from typing import Any

import pytest
from httpx import AsyncClient

from . import knowledge_fixtures as fx
from .conftest import SUITE_ON_POSTGRES, without_postgres

BASE = "/api/v1/orgs/varga/knowledge"
#: How the index ranks on the database the suite runs on: in the process on SQLite; on PostgreSQL,
#: hybrid when pgvector is there (required where the image carries it), full-text otherwise.
MODES = (
    {"in-process"}
    if not SUITE_ON_POSTGRES
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


def test_a_fenced_file_keeps_its_comments_as_code_and_every_chunk_reads_as_code() -> None:
    """S51-05: a YAML `# comment` is not a heading, and a long file is cut into fenced pieces."""
    from choregos_api.knowledge.chunking import chunk
    from choregos_api.knowledge.convert import fenced

    yaml = "".join(f"# service {i}\nservice_{i}:\n  replicas: {i}\n\n" for i in range(60))
    chunks = chunk(f"# deploy/values.yaml\n\n{fenced(yaml, 'yaml')}", "deploy/values.yaml", limit=300)
    assert len(chunks) > 1, "a long file is cut"
    assert all(c.heading_path == ["deploy/values.yaml"] for c in chunks), "a comment is no heading"
    for piece in chunks:
        assert piece.body.startswith("```yaml\n") and piece.body.endswith("\n```"), piece.body
        assert len(piece.body) <= 300
    inner = "\n".join(c.body.removeprefix("```yaml\n").removesuffix("\n```") for c in chunks)
    assert [line for line in inner.splitlines() if line] == [line for line in yaml.splitlines() if line]


def test_a_fence_is_longer_than_any_backticks_it_holds() -> None:
    from choregos_api.knowledge.convert import fenced

    assert fenced('\ufeff{"md": "````"}\r\n', "json") == '`````json\n{"md": "````"}\n`````\n'


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


async def test_the_gateway_embedder_tries_a_timed_out_batch_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """A slow answer from the gateway (seen in production: a reindex lost to one `ReadTimeout`)."""
    import httpx
    from choregos_api.knowledge.embeddings import GatewayEmbedder

    calls: list[int] = []

    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.5, 0.5]}]})

    monkeypatch.setattr(GatewayEmbedder, "RETRY_DELAYS", (0.0, 0.0))
    embedder = GatewayEmbedder("http://gateway", "key", "embed", transport=httpx.MockTransport(answer))
    assert await embedder.embed(["one"]) == [[0.5, 0.5]]
    assert len(calls) == 2


async def test_the_gateway_embedder_does_not_retry_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 422 (a budget exceeded, a refused key) would only be refused again."""
    import httpx
    from choregos_api.knowledge.embeddings import GatewayEmbedder

    calls: list[int] = []

    def answer(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(422, json={"error": {"type": "budget_exceeded"}})

    monkeypatch.setattr(GatewayEmbedder, "RETRY_DELAYS", (0.0, 0.0))
    embedder = GatewayEmbedder("http://gateway", "key", "embed", transport=httpx.MockTransport(answer))
    with pytest.raises(httpx.HTTPStatusError):
        await embedder.embed(["one"])
    assert len(calls) == 1


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


async def test_a_search_whose_embedding_the_gateway_refuses_ranks_by_words(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gateway refused a query's embedding (422, seen in production): the search answers anyway,
    ranked by its words, instead of a 500 that leaves the run's agent without its sources."""
    import httpx
    from choregos_api.knowledge import index

    await _indexed_base(client)

    class Refusing:
        dimensions = 64

        async def embed(self, texts: list[str]) -> list[list[float]]:
            request = httpx.Request("POST", "http://litellm:4000/v1/embeddings")
            raise httpx.HTTPStatusError("422", request=request, response=httpx.Response(422, request=request))

    monkeypatch.setattr(index, "get_embedder", lambda *a, **k: Refusing())
    found = await client.post(f"{BASE}/framework/search", json={"query": "Which TLS version in transit?"})
    assert found.status_code == 200, found.text
    assert found.json()["hits"][0]["filename"] == "encryption.docx"


async def test_a_reindex_keeps_every_document_it_can_embed(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One document the gateway cannot embed leaves only that document out, not the whole base."""
    import httpx
    from choregos_api.db.models import KnowledgeBase
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge import index
    from sqlalchemy import select

    base = await _indexed_base(client)
    real = index.index_document
    failing: list[str] = []

    async def index_document(org: str, document_id: str, embedder: object = None) -> int:
        if not failing:
            failing.append(document_id)
            raise httpx.ReadTimeout("slow")
        return await real(org, document_id)

    monkeypatch.setattr(index, "index_document", index_document)
    async with session_scope() as session:
        base_id = (
            await session.execute(select(KnowledgeBase.id).where(KnowledgeBase.slug == base["slug"]))
        ).scalar_one()
    assert await index.reindex_base("varga", base_id) > 0
    rebuilt = (await client.get(f"{BASE}/framework")).json()
    assert rebuilt["index"]["documents"] == 4, rebuilt["index"]


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


async def test_the_eval_measures_the_first_hit_and_the_section_that_answers(
    client: AsyncClient, admin: str
) -> None:
    """Recall@5 says the right document is somewhere in five passages; hit@1 says whether the first one
    is it, and section recall whether the passage found is the part of the document that answers."""
    await _indexed_base(client)
    cases = [
        {**EVAL_SET[0], "section": "Encryption standard > In transit"},
        {**EVAL_SET[4], "section": "certificate EXPIRED in production"},
        {**EVAL_SET[1], "section": "Key rotation"},  # no such section: not found, and said
        EVAL_SET[2],  # names no section: not counted in the section recall
    ]
    assert (await client.put(f"{BASE}/framework/eval", json={"cases": cases})).status_code == 200
    stored = (await client.get(f"{BASE}/framework")).json()["eval_set"]
    assert stored[0]["section"] == "Encryption standard > In transit" and stored[3]["section"] is None
    result = (await client.post(f"{BASE}/framework/eval/run")).json()["eval_result"]
    assert result["section_recall"] == round(2 / 3, 4), result["cases"]
    assert [c["section_found"] for c in result["cases"]] == [True, True, False, None]
    assert result["hit_at_1"] == sum(c["first"] in c["expected"] for c in result["cases"]) / 4
    assert result["hit_at_1"] >= 0.75, result["cases"]
    assert result["cases"][0]["first"] == "encryption.docx"


def test_a_hit_is_in_a_section_when_its_headings_run_through_it() -> None:
    from choregos_api.knowledge.index import in_section

    path = ["Security policy", "Encryption", "In transit"]
    assert in_section(path, "Encryption > In transit")
    assert in_section(path, "in  TRANSIT")
    assert in_section(path, "Encryption"), "a chunk of a subsection is in its parent section"
    assert not in_section(path, "In transit > Encryption")
    assert not in_section(path, "At rest")
    assert not in_section(path, " > ")
    assert not in_section([], "Encryption")


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


async def test_a_run_reads_a_cited_document_by_section_and_only_from_its_bases(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    """The sidecar's `knowledge_read` (ADR 0051 decision 33): the section a search hit cites, read whole,
    from the run's `index` bases only, while they are served to its project."""
    from choregos_api.db.models import KnowledgeDocument, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge.reading import READ_CHARS
    from choregos_api.security import mint_run_token

    await _indexed_base(client, "framework")
    other = await _indexed_base(client, "other-team", scope="projects", projects=["billing-api"])
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="BILLING-9", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="run-read", work_item_id=item.id, project_id=project["id"], stage_role="implement",
                status="running", stage_input={"knowledge": [{"slug": "framework", "mode": "index"}]},
            )
        )  # fmt: skip
    headers = {
        "Authorization": "Bearer "
        + mint_run_token("run-read", project_slug="billing-api", work_item_key="BILLING-9", ttl_minutes=30)
    }
    route = "/api/v1/internal/runs/run-read/knowledge"
    found = await client.post(f"{route}/search", json={"query": "TLS version in transit"}, headers=headers)
    hit = next(h for h in found.json()["hits"] if h["filename"] == "encryption.docx")

    async def read(**body: Any) -> Any:
        return await client.post(
            f"{route}/read", json={"knowledge_base": "framework", **body}, headers=headers
        )

    section = await read(document_id=hit["document_id"], section=" > ".join(hit["heading_path"]))
    assert section.status_code == 200, section.text
    answer = section.json()
    assert answer["found"] and answer["section"] == "1.1" and answer["filename"] == "encryption.docx"
    assert answer["heading_path"] == ["Encryption standard", "In transit"] and not answer["cut"]
    assert "TLS 1.3 is required" in answer["text"] and "vault" not in answer["text"], "that section only"
    assert answer["version"]

    whole = (await read(document_id=hit["document_id"])).json()
    assert whole["section"] is None and "vault" in whole["text"] and "TLS 1.3" in whole["text"]

    missing = (await read(document_id=hit["document_id"], section="Key rotation")).json()
    assert not missing["found"] and missing["text"] == ""
    assert "1.1 In transit" in missing["outline"] and "1.2 At rest" in missing["outline"]

    async with session_scope() as session:
        document = await session.get(KnowledgeDocument, hit["document_id"])
        assert document is not None
        document.markdown = "# Long\n\n## Part one\n\n" + "x " * READ_CHARS + "\n\n## Part two\n\nend\n"
    cut = (await read(document_id=hit["document_id"])).json()
    assert cut["cut"] and len(cut["text"]) == READ_CHARS and cut["characters"] > READ_CHARS
    assert "1.1 Part one" in cut["outline"] and "1.2 Part two" in cut["outline"]

    # Read as it is served (ADR 0174): with its OKF header, whose `# comment` is no section.
    async with session_scope() as session:
        document = await session.get(KnowledgeDocument, hit["document_id"])
        assert document is not None
        document.markdown = "---\ntype: Policy\n# reviewed yearly\n---\n# Policy\n\n## Scope\n\nAll.\n"
    served = (await read(document_id=hit["document_id"])).json()
    assert served["text"].startswith("---\n") and "type: Policy" in served["text"]
    scope = (await read(document_id=hit["document_id"], section="1.1")).json()
    assert scope["heading_path"] == ["Policy", "Scope"] and scope["text"].startswith("## Scope")
    # A document that declares itself withdrawn is not read, as it is neither served nor indexed.
    async with session_scope() as session:
        document = await session.get(KnowledgeDocument, hit["document_id"])
        assert document is not None
        document.markdown = "---\nstatus: deprecated\n---\n# Policy\n"
    assert (await read(document_id=hit["document_id"])).status_code == 404

    # A base the run does not name, a document of another base, an unknown document: the same 404.
    other_document = other["documents"][0]["id"]
    assert (await read(document_id=other_document)).status_code == 404
    elsewhere = await client.post(
        f"{route}/read", json={"knowledge_base": "other-team", "document_id": other_document}, headers=headers
    )
    assert elsewhere.status_code == 404
    assert (await read(document_id="nope")).status_code == 404
    await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": "web", "name": "Web", "config": {"slug": "web", "org": "varga"}},
    )
    await client.patch(f"{BASE}/framework", json={"scope": "projects", "projects": ["web"]})
    assert (await read(document_id=hit["document_id"])).status_code == 404, "no longer served: not read"


# ───────────────────────────── PostgreSQL ─────────────────────────────


@without_postgres
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
