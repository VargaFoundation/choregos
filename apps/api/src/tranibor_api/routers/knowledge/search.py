# SPDX-License-Identifier: Apache-2.0
"""Searching a knowledge base, its index and its evaluation set (ADR 0051)."""

from __future__ import annotations

from dataclasses import asdict
from functools import partial

from fastapi import status

from ...audit import record
from ...deps import Db, Me
from ...errors import conflict, unprocessable
from ...knowledge.access import (
    KnowledgeAction,
)
from ...knowledge.conversion import conversions
from ...knowledge.index import evaluate, reindex_base, search
from ...schemas.knowledge import (
    KnowledgeBaseDto,
    KnowledgeEvalSet,
    KnowledgeSearchRequest,
    KnowledgeSearchResult,
)
from ._common import _base, _dto, _member, _require
from ._router import Slug, router


@router.post(
    "/orgs/{org}/knowledge/{slug}/search",
    response_model=KnowledgeSearchResult,
    operation_id="searchKnowledgeBase",
)
async def search_knowledge_base(
    org: str, slug: Slug, body: KnowledgeSearchRequest, session: Db, principal: Me
) -> KnowledgeSearchResult:
    """The hybrid search of an `index` base, with its citations — what `knowledge_search` gives agents."""
    _member(principal, org)
    base = await _base(session, principal, org, slug)
    if base.serving_mode != "index":
        raise conflict(f"knowledge base `{slug}` is served as files: it has no index to search")
    result = await search(session, [base], body.query, k=body.k)
    return KnowledgeSearchResult.model_validate(
        {"mode": result.mode, "hits": [asdict(h) for h in result.hits]}
    )


@router.post(
    "/orgs/{org}/knowledge/{slug}/reindex",
    response_model=KnowledgeBaseDto,
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="reindexKnowledgeBase",
)
async def reindex_knowledge_base(org: str, slug: Slug, session: Db, principal: Me) -> KnowledgeBaseDto:
    """Cuts and embeds every ready document again, in the background (after pgvector was installed,
    or the embedding model changed)."""
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.EDIT)
    if base.serving_mode != "index":
        raise conflict(f"knowledge base `{slug}` is served as files: switch it to `index` first")
    dto = await _dto(session, principal, org, base, with_documents=True)
    await session.commit()
    conversions.spawn("reindex", partial(reindex_base, org, base.id))
    return dto


@router.put(
    "/orgs/{org}/knowledge/{slug}/eval",
    response_model=KnowledgeBaseDto,
    operation_id="putKnowledgeEvalSet",
)
async def put_knowledge_eval_set(
    org: str, slug: Slug, body: KnowledgeEvalSet, session: Db, principal: Me
) -> KnowledgeBaseDto:
    """The base's eval set: questions, and the documents expected to answer each."""
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.EDIT)
    base.eval_set = [case.model_dump() for case in body.cases]
    base.eval_result = None
    await session.flush()
    return await _dto(session, principal, org, base)


@router.post(
    "/orgs/{org}/knowledge/{slug}/eval/run",
    response_model=KnowledgeBaseDto,
    operation_id="runKnowledgeEval",
)
async def run_knowledge_eval(org: str, slug: Slug, session: Db, principal: Me) -> KnowledgeBaseDto:
    """Runs the eval set against the index: recall@5, per question. Shown on the base's page."""
    base = await _base(session, principal, org, slug)
    _require(principal, org, base, KnowledgeAction.EDIT)
    if base.serving_mode != "index":
        raise conflict(f"knowledge base `{slug}` is served as files: it has no index to evaluate")
    if not base.eval_set:
        raise unprocessable("the eval set is empty: give questions and their expected documents first")
    base.eval_result = await evaluate(session, base, k=5)
    await session.flush()
    await record(
        session,
        principal,
        "knowledge.eval",
        org_id=base.org_id,
        target_type="knowledge_base",
        target_id=base.id,
        recall=base.eval_result["recall"],
    )
    return await _dto(session, principal, org, base)
