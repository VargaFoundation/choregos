# SPDX-License-Identifier: Apache-2.0
"""Mémoire : recherche, file des faits proposés, réimport."""

from __future__ import annotations

from typing import Annotated, Any

from choregos_core.domain import Memory
from fastapi import APIRouter, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..deps import Db, Me, ProjectContext, ProjectCtx
from ..errors import forbidden
from ..rbac import Permission
from ..schemas import MemoryAbGroup, MemoryAbReport, MemoryDecision, MemoryDto, MemoryReimport
from ..services import effective_policy
from ..services.memory_access import project_adapter, project_namespace
from ..temporal import get_temporal

router = APIRouter(tags=["memory"])


async def _adapter(ctx: ProjectContext, session: AsyncSession) -> Any:
    """La mémoire du projet, selon SON connecteur `memory` — pas Ecphoria d'office.

    Le type était codé en dur : un projet configuré en `lexical` (le repli sans service
    de plus) lisait Ecphoria ici et le repli dans l'orchestrateur. L'organisation est
    passée à la fabrique : sur PostgreSQL, la mémoire lexicale ne voit que ce que la RLS
    montre à cette organisation.
    """
    return await project_adapter(session, ctx.id, ctx.slug, ctx.org_slug)


def to_dto(memory: Memory) -> MemoryDto:
    return MemoryDto(
        id=memory.id,
        kind=str(memory.kind),
        subject=memory.subject,
        content=memory.content,
        score=memory.score,
        status=memory.status,
        valid_from=memory.valid_from,
        valid_to=memory.valid_to,
        provenance=memory.provenance,
        proposed_by=memory.proposed_by,
    )


@router.get("/projects/{id}/memory/search", response_model=list[MemoryDto], operation_id="searchMemory")
async def search(
    ctx: ProjectCtx,
    session: Db,
    q: Annotated[str, Query()],
    k: Annotated[int, Query(le=100)] = 10,
    kinds: Annotated[str | None, Query()] = None,
    memory: Annotated[str | None, Query()] = None,
    agent: Annotated[str | None, Query()] = None,
) -> list[MemoryDto]:
    policy = await effective_policy(session, ctx.id)
    if not policy.memory.enabled:
        return []
    adapter = await _adapter(ctx, session)
    results = await adapter.search(await project_namespace(session, ctx.project, memory, agent), q, k)
    wanted = {kind.strip() for kind in (kinds or "").split(",") if kind.strip()}
    return [to_dto(m) for m in results if not wanted or str(m.kind) in wanted]


@router.get("/projects/{id}/memory/pending", response_model=list[MemoryDto], operation_id="listPendingMemory")
async def pending(
    ctx: ProjectCtx,
    session: Db,
    memory: Annotated[str | None, Query()] = None,
    agent: Annotated[str | None, Query()] = None,
) -> list[MemoryDto]:
    """Facts proposed by agents, waiting for a human decision (governed write).

    `?memory=` (S40-11): `shared` (the default), `own` with `&agent=<slug>`, or a named memory of the project.
    """
    namespace = await project_namespace(session, ctx.project, memory, agent)
    adapter = await _adapter(ctx, session)
    rows = getattr(adapter, "pending", {}).get(namespace, []) if hasattr(adapter, "pending") else []
    if hasattr(adapter, "list_pending"):
        rows = await adapter.list_pending(namespace)
    return [to_dto(m) for m in rows]


@router.post("/projects/{id}/memory/pending", operation_id="decidePendingMemory")
async def decide(
    ctx: ProjectCtx,
    body: MemoryDecision,
    session: Db,
    memory: Annotated[str | None, Query()] = None,
    agent: Annotated[str | None, Query()] = None,
) -> dict[str, str]:
    ctx.require(Permission.MEMORY_WRITE)
    namespace = await project_namespace(session, ctx.project, memory, agent)
    adapter = await _adapter(ctx, session)
    if body.action == "accept":
        accepted = await adapter.accept_pending(namespace, body.id)
    else:
        accepted = (
            await adapter.reject_pending(namespace, body.id) if hasattr(adapter, "reject_pending") else True
        )
    await record(
        session,
        ctx.principal,
        f"memory.{body.action}",
        org_id=ctx.project.org_id,
        target_type="memory",
        target_id=body.id,
        reason=body.reason,
    )
    return {"status": "accepted" if accepted else "not_found"}


@router.post(
    "/projects/{id}/memory/reimport", status_code=status.HTTP_202_ACCEPTED, operation_id="reimportMemory"
)
async def reimport(ctx: ProjectCtx, body: MemoryReimport, session: Db) -> dict[str, str]:
    ctx.require(Permission.MEMORY_WRITE)
    await get_temporal().signal(
        f"mem-{ctx.slug}", "reimport", {"sources": body.sources or ["readme", "docs", "adr", "closed_issues"]}
    )
    await record(
        session,
        ctx.principal,
        "memory.reimport",
        org_id=ctx.project.org_id,
        target_type="project",
        target_id=ctx.id,
    )
    return {"status": "accepted"}


@router.get("/orgs/{org}/memory/ab-report", response_model=MemoryAbReport, operation_id="getMemoryAbReport")
async def ab_report(
    org: str,
    session: Db,
    principal: Me,
    weeks: Annotated[int, Query(ge=1, le=52)] = 4,
) -> MemoryAbReport:
    """Compare les projets avec et sans mémoire sur le premier passage et le coût par ticket.

    Le même calcul que le rapport hebdomadaire envoyé par `MemoryIngestion` : tant qu'un
    groupe a moins de dix tickets fermés sur la fenêtre, le verdict reste « échantillon
    insuffisant » plutôt qu'une conclusion prise sur trois tickets.
    """
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden()
    from ..services import memory_ab_comparison

    raw = await memory_ab_comparison(session, org, weeks)
    groups = raw.get("groups", {})
    return MemoryAbReport(
        org=org,
        weeks=weeks,
        since=raw.get("since"),
        with_memory=MemoryAbGroup(**groups.get("with_memory", {})),
        without_memory=MemoryAbGroup(**groups.get("without_memory", {})),
        verdict=str(raw.get("verdict", "inconnu")),
        detail=str(raw.get("detail", "")),
    )
