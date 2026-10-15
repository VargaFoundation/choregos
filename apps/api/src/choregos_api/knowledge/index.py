# SPDX-License-Identifier: Apache-2.0
"""The knowledge index: contextual chunks, and a hybrid search that cites (S33-03, ADR 0051).

Three ways to rank, chosen by what the database offers — and the answer always says which (`mode`):

- `hybrid`: PostgreSQL full-text rank (`ts_rank_cd` over `search_tsv`) and pgvector cosine distance
  (`embedding_vec <=>`), fused by reciprocal rank;
- `full-text`: PostgreSQL without pgvector, or no embedder (no gateway key): the full-text rank alone;
- `in-process`: SQLite (development, tests): a BM25 rank and cosine over the JSON embeddings, fused
  the same way, computed in the process.

A search only ever reads the knowledge bases it is given; who may search which base is decided by
the caller (`access.py`): the console by the human's scope, the internal API by the run's project.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import bindparam, delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from ..config import get_settings
from ..db.models import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from ..db.session import session_scope
from ..logging import get_logger
from .chunking import chunk
from .embeddings import Embedder, check_model_usage, cosine, embedding_model, get_embedder, words

logger = get_logger("choregos.knowledge.index")

HYBRID, FULL_TEXT, IN_PROCESS = "hybrid", "full-text", "in-process"
#: How many candidates each ranking proposes before the fusion.
CANDIDATES = 50
#: Reciprocal rank fusion constant (Cormack et al., 2009).
RRF_K = 60
EXCERPT = 600


@dataclass
class Hit:
    knowledge_base: str
    document_id: str
    document: str
    filename: str
    heading_path: list[str]
    excerpt: str
    score: float


@dataclass
class SearchResult:
    mode: str
    hits: list[Hit] = field(default_factory=list)


async def has_vector_column(session: AsyncSession) -> bool:
    """Whether the migration could give the chunks a pgvector column (never on SQLite)."""
    if get_settings().is_sqlite:
        return False
    found = await session.execute(
        text(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'knowledge_chunks' AND column_name = 'embedding_vec'"
        )
    )
    return found.scalar() is not None


async def search_mode(session: AsyncSession, embedder: Embedder | None = None) -> str:
    if get_settings().is_sqlite:
        return IN_PROCESS
    embedder = embedder if embedder is not None else get_embedder()
    return HYBRID if embedder is not None and await has_vector_column(session) else FULL_TEXT


def _vector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{v:.7g}" for v in vector) + "]"


# ───────────────────────────── indexing ─────────────────────────────


async def index_document(org: str, document_id: str, embedder: Embedder | None = None) -> int:
    """(Re)builds the chunks of one ready document of an `index` base; returns how many."""
    async with session_scope(orgs=[org]) as session:
        document = (
            await session.execute(
                select(KnowledgeDocument)
                .options(undefer(KnowledgeDocument.markdown))
                .where(KnowledgeDocument.id == document_id)
            )
        ).scalar_one_or_none()
        if document is None:
            return 0
        base = await session.get(KnowledgeBase, document.knowledge_base_id)
        await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id))
        if base is None or base.serving_mode != "index" or document.status != "ready":
            return 0
        chunks = chunk(document.markdown or "", document.title)
        if not chunks:
            return 0
        embedder = embedder if embedder is not None else get_embedder()
        vectors: list[list[float]] | None = None
        mode = await search_mode(session, embedder)
        if embedder is not None and mode != FULL_TEXT:
            # The organisation's catalogue decides whether this model may embed (ADR 0060).
            await check_model_usage(session, document.org_id, embedding_model(get_settings()), "embed")
            vectors = await embedder.embed([c.text for c in chunks])
        rows = [
            KnowledgeChunk(
                org_id=document.org_id,
                knowledge_base_id=base.id,
                document_id=document.id,
                ordinal=c.ordinal,
                heading_path=c.heading_path,
                body=c.body,
                text=c.text,
                embedding=vectors[i] if vectors is not None and mode == IN_PROCESS else None,
            )
            for i, c in enumerate(chunks)
        ]
        session.add_all(rows)
        await session.flush()
        if vectors is not None and mode == HYBRID:
            await session.execute(
                text("UPDATE knowledge_chunks SET embedding_vec = CAST(:v AS vector) WHERE id = :id"),
                [{"id": row.id, "v": _vector_literal(vectors[i])} for i, row in enumerate(rows)],
            )
    logger.info("knowledge.document.indexed", org=org, document=document_id, chunks=len(rows), mode=mode)
    return len(rows)


async def reindex_base(org: str, base_id: str) -> int:
    """Rebuilds every ready document of a base (after a switch to `index`, or a new embedder)."""
    async with session_scope(orgs=[org]) as session:
        ids = list(
            (
                await session.execute(
                    select(KnowledgeDocument.id).where(
                        KnowledgeDocument.knowledge_base_id == base_id, KnowledgeDocument.status == "ready"
                    )
                )
            ).scalars()
        )
        await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.knowledge_base_id == base_id))
    total = 0
    for document_id in ids:
        total += await index_document(org, document_id)
    return total


# ───────────────────────────── search ─────────────────────────────


def fuse(*rankings: list[str], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: each ranking gives `1 / (k + rank)` to what it ranks."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def bm25(query: str, documents: dict[str, str], *, k1: float = 1.2, b: float = 0.75) -> list[str]:
    """Okapi BM25 over the given texts: the keys, best first (only those sharing a word)."""
    terms = set(words(query))
    if not terms or not documents:
        return []
    tokenised = {key: words(body) for key, body in documents.items()}
    average = sum(len(t) for t in tokenised.values()) / len(tokenised) or 1.0
    frequency = Counter(term for tokens in tokenised.values() for term in set(tokens) & terms)
    scored: list[tuple[float, str]] = []
    for key, tokens in tokenised.items():
        counts = Counter(tokens)
        score = 0.0
        for term in terms & counts.keys():
            idf = math.log(1 + (len(tokenised) - frequency[term] + 0.5) / (frequency[term] + 0.5))
            tf = counts[term]
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * len(tokens) / average))
        if score > 0:
            scored.append((score, key))
    return [key for _, key in sorted(scored, key=lambda item: (-item[0], item[1]))]


async def _full_text(session: AsyncSession, base_ids: list[str], query: str) -> list[str]:
    terms = sorted(set(words(query)))
    if not terms:
        return []
    rows = await session.execute(
        text(
            "SELECT id FROM knowledge_chunks, to_tsquery('simple', :q) AS q "
            "WHERE knowledge_base_id IN :bases AND search_tsv @@ q "
            "ORDER BY ts_rank_cd(search_tsv, q) DESC, id LIMIT :n"
        ).bindparams(bindparam("bases", expanding=True)),
        {"q": " | ".join(terms), "bases": base_ids, "n": CANDIDATES},
    )
    return [str(row[0]) for row in rows]


async def _nearest(session: AsyncSession, base_ids: list[str], vector: list[float]) -> list[str]:
    rows = await session.execute(
        text(
            "SELECT id FROM knowledge_chunks "
            "WHERE knowledge_base_id IN :bases AND embedding_vec IS NOT NULL "
            "AND vector_dims(embedding_vec) = :dims "
            "ORDER BY embedding_vec <=> CAST(:v AS vector), id LIMIT :n"
        ).bindparams(bindparam("bases", expanding=True)),
        {"bases": base_ids, "dims": len(vector), "v": _vector_literal(vector), "n": CANDIDATES},
    )
    return [str(row[0]) for row in rows]


async def _in_process(
    session: AsyncSession, base_ids: list[str], query: str, embedder: Embedder | None
) -> list[list[str]]:
    rows = list(
        (
            await session.execute(
                select(KnowledgeChunk.id, KnowledgeChunk.text, KnowledgeChunk.embedding).where(
                    KnowledgeChunk.knowledge_base_id.in_(base_ids)
                )
            )
        ).tuples()
    )
    lexical = bm25(query, {str(i): t for i, t, _ in rows})[:CANDIDATES]
    if embedder is None:
        return [lexical]
    (vector,) = await embedder.embed([query])
    scored = sorted(
        ((cosine(vector, list(e or [])), str(i)) for i, _, e in rows if e),
        key=lambda item: (-item[0], item[1]),
    )
    return [lexical, [key for score, key in scored[:CANDIDATES] if score > 0]]


async def search(
    session: AsyncSession, bases: list[Any], query: str, *, k: int = 5, embedder: Embedder | None = None
) -> SearchResult:
    """The `k` best chunks of these bases for `query`, each with what a citation needs."""
    embedder = embedder if embedder is not None else get_embedder()
    mode = await search_mode(session, embedder)
    by_id = {base.id: base for base in bases if base.serving_mode == "index"}
    if not by_id or not query.strip():
        return SearchResult(mode=mode)
    base_ids = sorted(by_id)
    if mode == IN_PROCESS:
        rankings = await _in_process(session, base_ids, query, embedder)
    else:
        rankings = [await _full_text(session, base_ids, query)]
        if mode == HYBRID and embedder is not None:
            (vector,) = await embedder.embed([query])
            rankings.append(await _nearest(session, base_ids, vector))
    best = fuse(*rankings)[: max(1, min(k, 20))]
    if not best:
        return SearchResult(mode=mode)
    chunks = {
        row.id: (row, document)
        for row, document in (
            await session.execute(
                select(KnowledgeChunk, KnowledgeDocument)
                .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
                .where(KnowledgeChunk.id.in_([key for key, _ in best]))
            )
        ).tuples()
    }
    hits = []
    for key, score in best:
        row, document = chunks[key]
        hits.append(
            Hit(
                knowledge_base=by_id[row.knowledge_base_id].slug,
                document_id=document.id,
                document=document.title,
                filename=document.filename,
                heading_path=list(row.heading_path or []),
                excerpt=row.body[:EXCERPT],
                score=round(score, 6),
            )
        )
    return SearchResult(mode=mode, hits=hits)


async def evaluate(session: AsyncSession, base: Any, *, k: int = 5) -> dict[str, Any]:
    """Runs the base's eval set: for each question, are the expected documents in the top `k`?

    recall@k = expected documents found in the top `k` hits / expected documents, over all questions.
    A document is named by its file name or its title.
    """
    from choregos_core import utcnow

    cases: list[dict[str, Any]] = []
    expected_total = found_total = 0
    mode = IN_PROCESS
    for case in base.eval_set or []:
        result = await search(session, [base], str(case.get("question", "")), k=k)
        mode = result.mode
        returned = {name for hit in result.hits for name in (hit.filename, hit.document)}
        expected = [str(name) for name in case.get("expected", [])]
        found = [name for name in expected if name in returned]
        expected_total += len(expected)
        found_total += len(found)
        cases.append(
            {
                "question": case.get("question", ""),
                "expected": expected,
                "found": found,
                "returned": list(dict.fromkeys(hit.filename for hit in result.hits)),
            }
        )
    return {
        "k": k,
        "recall": round(found_total / expected_total, 4) if expected_total else None,
        "mode": mode,
        "cases": cases,
        "ran_at": utcnow().isoformat(),
    }
