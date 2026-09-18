"""Mémoire : recherche, file des faits proposés, réimport."""

from __future__ import annotations

from typing import Annotated

from choregos_core.domain import Memory
from fastapi import APIRouter, Query, status

from ..audit import record
from ..deps import Db, Me, ProjectCtx
from ..errors import forbidden
from ..rbac import Permission
from ..schemas import MemoryAbGroup, MemoryAbReport, MemoryDecision, MemoryDto, MemoryReimport
from ..services import active_policy, policy_model
from ..temporal import get_temporal

router = APIRouter(tags=["memory"])


def _adapter(project_slug: str) -> object:
    from choregos_adapters import build

    return build("memory", "ecphoria", {"tenant": project_slug})


def _dto(memory: Memory) -> MemoryDto:
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
) -> list[MemoryDto]:
    policy = policy_model(await active_policy(session, ctx.id))
    if not policy.memory.enabled:
        return []
    adapter = _adapter(ctx.slug)
    results = await adapter.search(ctx.slug, q, k)  # type: ignore[attr-defined]
    wanted = {kind.strip() for kind in (kinds or "").split(",") if kind.strip()}
    return [_dto(m) for m in results if not wanted or str(m.kind) in wanted]


@router.get("/projects/{id}/memory/pending", response_model=list[MemoryDto], operation_id="listPendingMemory")
async def pending(ctx: ProjectCtx) -> list[MemoryDto]:
    """Faits proposés par des agents, en attente de validation humaine (écriture gouvernée)."""
    adapter = _adapter(ctx.slug)
    rows = getattr(adapter, "pending", {}).get(ctx.slug, []) if hasattr(adapter, "pending") else []
    if hasattr(adapter, "list_pending"):
        rows = await adapter.list_pending(ctx.slug)
    return [_dto(m) for m in rows]


@router.post("/projects/{id}/memory/pending", operation_id="decidePendingMemory")
async def decide(ctx: ProjectCtx, body: MemoryDecision, session: Db) -> dict[str, str]:
    ctx.require(Permission.MEMORY_WRITE)
    adapter = _adapter(ctx.slug)
    if body.action == "accept":
        accepted = await adapter.accept_pending(ctx.slug, body.id)  # type: ignore[attr-defined]
    else:
        accepted = (
            await adapter.reject_pending(ctx.slug, body.id) if hasattr(adapter, "reject_pending") else True
        )
    await record(
        session,
        ctx.principal,
        f"memory.{body.action}",
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
    await record(session, ctx.principal, "memory.reimport", target_type="project", target_id=ctx.id)
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
