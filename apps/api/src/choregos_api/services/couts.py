# SPDX-License-Identifier: Apache-2.0
"""Le coût : l'estimation d'un ticket depuis ses semblables, et une ligne du registre."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from choregos_contracts import ChoregosEvent, EventType
from choregos_core import (
    utcnow,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    CostLedger,
    WorkItem,
)
from ..events import diffuser
from ..schemas import (
    CostEstimate,
)


async def estimate_cost(session: AsyncSession, project_id: str, item: WorkItem) -> CostEstimate | None:
    """Médiane et p80 des tickets comparables sur 90 jours (S4-04)."""
    if not item.size:
        return None
    since = datetime.now(UTC) - timedelta(days=90)
    rows = (
        (
            await session.execute(
                select(WorkItem.totals).where(
                    WorkItem.project_id == project_id,
                    WorkItem.size == item.size,
                    WorkItem.closed_at.is_not(None),
                    WorkItem.closed_at >= since,
                )
            )
        )
        .scalars()
        .all()
    )
    costs = sorted(float((t or {}).get("cost_usd", 0.0)) for t in rows if t)
    costs = [c for c in costs if c > 0]
    if not costs:
        return None
    median = costs[len(costs) // 2]
    p80 = costs[min(len(costs) - 1, int(len(costs) * 0.8))]
    spent = float((item.totals or {}).get("cost_usd", 0.0))
    return CostEstimate(median_usd=median, p80_usd=p80, sample_size=len(costs), over_p80=spent > p80)


async def dimensions_of(session: AsyncSession, run_id: str | None) -> dict[str, Any]:
    """The agent and workflow a run belongs to, for a ledger line (S29-01). Empty without a run."""
    from ..db.models import Run, WorkflowDef, WorkItem

    run = await session.get(Run, run_id) if run_id else None
    if run is None:
        return {}
    dims: dict[str, Any] = {"agent_slug": run.agent_slug, "agent_version": run.agent_version}
    item = await session.get(WorkItem, run.work_item_id) if run.work_item_id else None
    definition = (
        await session.get(WorkflowDef, item.workflow_def_id) if item and item.workflow_def_id else None
    )
    if definition is not None:
        dims |= {"workflow_name": definition.name, "workflow_version": definition.version}
    return dims


async def record_cost(  # noqa: PLR0913 — une ligne du registre, champ par champ, en mots-clés
    session: AsyncSession,
    *,
    project_id: str,
    work_item_id: str | None,
    run_id: str | None,
    provider: str,
    model: str,
    backend: str | None,
    stage_role: str | None,
    size: str | None,
    tokens_in: int,
    tokens_out: int,
    tokens_cached: int,
    cost_usd: float,
    fx_rate: float,
    kind: str = "model",
) -> CostLedger:
    entry = CostLedger(
        kind=kind,
        **await dimensions_of(session, run_id),
        project_id=project_id,
        work_item_id=work_item_id,
        run_id=run_id,
        provider=provider,
        model=model,
        backend=backend,
        stage_role=stage_role,
        size=size,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tokens_cached=tokens_cached,
        cost_usd=cost_usd,
        cost_eur=round(cost_usd * fx_rate, 6),
        fx_rate=fx_rate,
        ts=utcnow(),
    )
    session.add(entry)
    await diffuser(
        session,
        ChoregosEvent.emit(
            EventType.COST_RECORDED,
            source="/choregos/api",
            subject=run_id,
            cost_usd=cost_usd,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
        ),
    )
    # A budget this spend has just crossed becomes an inbox item (S29-04).
    from .budget_alerts import check_after_spend

    await check_after_spend(session, run_id)
    return entry
