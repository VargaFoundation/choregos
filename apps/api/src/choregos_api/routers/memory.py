# SPDX-License-Identifier: Apache-2.0
"""Memory: search, queue of proposed facts, re-import."""

from __future__ import annotations

from typing import Annotated, Any

from choregos_core.domain import Memory
from fastapi import APIRouter, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..audit import record
from ..deps import Db, Me, ProjectContext, ProjectCtx
from ..errors import conflict, forbidden
from ..rbac import Permission
from ..schemas import MemoryAbGroup, MemoryAbReport, MemoryDecision, MemoryDto, MemoryReimport
from ..services import effective_policy, policy_engine
from ..services.memory_access import project_adapter, project_namespace
from ..temporal import MemoryLoopNotRunningError, signal_memory_loop

router = APIRouter(tags=["memory"])


async def _adapter(ctx: ProjectContext, session: AsyncSession) -> Any:
    """The project memory, according to ITS `memory` connector — not Ecphoria by default.

    The type used to be hard-coded: a project configured as `lexical` (the fallback with no extra
    service) read Ecphoria here and the fallback in the orchestrator. The organisation is
    passed to the factory: on PostgreSQL, the lexical memory only sees what the RLS
    shows to this organisation.
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
    if not (await policy_engine(session, ctx.id)).memory_enabled():
        raise conflict(
            f"project {ctx.slug}'s policy disables memory (memory.enabled: false): "
            "there is nothing to reimport"
        )
    # The loop is started when it does not run: a signal to a missing workflow answered 500 (S51-02).
    try:
        await signal_memory_loop(
            ctx.slug, "reimport", {"sources": body.sources or ["readme", "docs", "adr", "closed_issues"]}
        )
    except MemoryLoopNotRunningError as exc:
        raise conflict(
            f"the memory loop of project {ctx.slug} does not run and could not be started ({exc.cause}); "
            "an orchestrator restart starts the loop of every active project"
        ) from exc
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
    """Compare projects with and without memory on first pass and cost per ticket.

    The same computation as the weekly report sent by `MemoryIngestion`: as long as a
    group has fewer than ten closed tickets over the window, the verdict stays "insufficient
    sample" rather than a conclusion drawn from three tickets.
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
