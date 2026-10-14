# SPDX-License-Identifier: Apache-2.0
"""What is left to spend: the reservation a run's key is minted at (ADR 0047, S27-02).

A budget is a reservation, not a report. What is left under a cap is the cap, minus what the cost
ledger says was spent (models and tools), minus the keys still open — each open key may still
spend up to its own cap. A run's key is minted at the smallest of what is left; below a floor the
stage does not start.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time
from typing import Any

from choregos_core import utcnow
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import CostLedger, GatewayKeyRow, Run

#: Below this, a key is not worth minting: the stage would be refused at its first call (USD).
DEFAULT_FLOOR_USD = 0.01


def start_of_day() -> datetime:
    """Midnight UTC today: a day budget counts from here."""
    now = utcnow()
    return datetime.combine(now.date(), time.min, tzinfo=now.tzinfo)


def ticket_budget(engine: Any, item: Any) -> float:
    """The work item's budget: the policy's, for its CURRENT size (a refinement may change it), plus
    what people granted on top (S27-04)."""
    return float(engine.budget_ticket(item.size)) + float(item.budget_top_up_usd or 0.0)


async def ticket_left(session: AsyncSession, engine: Any, item: Any, exclude_run: str | None = None) -> float:
    """What the work item may still spend: its budget, minus the ledger, minus its open keys."""
    return (
        ticket_budget(engine, item)
        - await ticket_spent(session, item.id)
        - await open_reservations(session, work_item_id=item.id, exclude_run=exclude_run)
    )


async def ticket_spent(session: AsyncSession, work_item_id: str) -> float:
    """What the work item has cost, read from the ledger: model calls AND tool calls."""
    total = (
        await session.execute(
            select(func.coalesce(func.sum(CostLedger.cost_usd), 0.0)).where(
                CostLedger.work_item_id == work_item_id
            )
        )
    ).scalar_one()
    return float(total)


async def project_spent_since(session: AsyncSession, project_id: str, since: datetime) -> float:
    total = (
        await session.execute(
            select(func.coalesce(func.sum(CostLedger.cost_usd), 0.0)).where(
                CostLedger.project_id == project_id, CostLedger.ts >= since
            )
        )
    ).scalar_one()
    return float(total)


async def open_reservations(
    session: AsyncSession,
    *,
    work_item_id: str | None = None,
    project_id: str | None = None,
    exclude_run: str | None = None,
) -> float:
    """The caps of the keys still open — not revoked, not expired — for a work item or a project.

    `collect_spend` revokes a key when it books its spend in the ledger: a key is counted either
    as open or as spent, never both."""
    query = select(func.coalesce(func.sum(GatewayKeyRow.budget_usd), 0.0)).where(
        GatewayKeyRow.revoked.is_(False),
        or_(GatewayKeyRow.expires_at.is_(None), GatewayKeyRow.expires_at > utcnow()),
    )
    if work_item_id is not None:
        query = query.join(Run, Run.id == GatewayKeyRow.run_id).where(Run.work_item_id == work_item_id)
    if project_id is not None:
        query = query.where(GatewayKeyRow.project_id == project_id)
    if exclude_run is not None:
        query = query.where(or_(GatewayKeyRow.run_id.is_(None), GatewayKeyRow.run_id != exclude_run))
    return float((await session.execute(query)).scalar_one())


@dataclass(frozen=True)
class Reservation:
    """What a run may spend: the smallest of what is left under each cap that applies."""

    usd: float
    #: The cap that set it (`stage`, `ticket`, `project_day`, …).
    bound_by: str
    #: What is left under each cap that applies.
    left: dict[str, float] = field(default_factory=dict)

    def below(self, floor: float) -> bool:
        return self.usd <= 0 or self.usd < floor

    def describe(self) -> str:
        parts = ", ".join(f"{name} {value:.2f} $" for name, value in self.left.items())
        return f"{self.usd:.2f} $ left (bound by {self.bound_by}; {parts})"


def reserve(left: dict[str, float | None]) -> Reservation:
    """The reservation, from what is left under each cap; `None` means the cap does not apply.

    At least `stage` must apply. A negative remainder (a cap already overspent) reserves zero."""
    applied = {name: round(max(0.0, value), 6) for name, value in left.items() if value is not None}
    if not applied:
        raise ValueError("a reservation needs at least one cap")
    bound_by = min(applied, key=lambda name: applied[name])
    return Reservation(usd=applied[bound_by], bound_by=bound_by, left=applied)


__all__ = [
    "DEFAULT_FLOOR_USD",
    "Reservation",
    "open_reservations",
    "project_spent_since",
    "reserve",
    "start_of_day",
    "ticket_budget",
    "ticket_left",
    "ticket_spent",
]
