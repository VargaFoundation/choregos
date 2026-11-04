# SPDX-License-Identifier: Apache-2.0
"""Proof before dependence: the A/B comparison of projects with and without memory."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from choregos_core import (
    utcnow,
)
from choregos_core.compat import alias_names
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    Project,
    Run,
    WorkItem,
)
from ..db.org_aliases import organisation_by_slug_or_alias
from .definitions import effective_policy

MIN_WORK_ITEMS_PER_GROUP = 10
"""Below this, no conclusion is drawn: ten closed work items per arm, at least."""


async def memory_ab_comparison(session: AsyncSession, org: str, weeks: int = 4) -> dict[str, Any]:
    """Compare the projects **with** and **without** a context pack (docs/plan/04, decision 4).

    Memory is not a given: it has to pay. Over the requested window we measure the rate of PRs
    merged at the first pass and the cost per work item, group against group.
    """
    from statistics import median

    since = utcnow() - timedelta(weeks=weeks)
    buckets: dict[str, dict[str, Any]] = {
        "with_memory": {"projects": [], "rates": [], "costs": [], "tickets": 0},
        "without_memory": {"projects": [], "rates": [], "costs": [], "tickets": 0},
    }
    # `org` may be a slug the organisation had when a workflow was started (ADR 0090).
    organization = await organisation_by_slug_or_alias(session, org)
    if organization is not None:
        # Every project that is not archived counts: what decides the sample is the work items
        # closed over the window, not the administrative status of the project.
        projects = (
            (
                await session.execute(
                    select(Project).where(Project.org_id == organization.id, Project.status != "archived")
                )
            )
            .scalars()
            .all()
        )
        for project in projects:
            policy = await effective_policy(session, project.id)
            bucket = buckets["with_memory" if policy.memory.enabled else "without_memory"]
            stats = await _window_stats(session, project.id, since)
            if stats["tickets"] == 0:
                continue
            bucket["projects"].append(project.slug)
            bucket["tickets"] += stats["tickets"]
            if stats["first_pass_rate"] is not None:
                bucket["rates"].append(stats["first_pass_rate"])
            bucket["costs"].extend(stats["costs"])

    groups = {
        name: {
            "projects": sorted(bucket["projects"]),
            "tickets": bucket["tickets"],
            "first_pass_merge_rate": round(median(bucket["rates"]), 4) if bucket["rates"] else None,
            "cost_per_ticket_usd": round(median(bucket["costs"]), 4) if bucket["costs"] else None,
        }
        for name, bucket in buckets.items()
    }
    verdict, detail = _ab_verdict(groups)
    return {
        "org": org,
        "weeks": weeks,
        "since": since.isoformat(),
        "groups": groups,
        "verdict": "organisation inconnue"
        if organization is None
        else verdict,  # lang: fr-ok — returned, S45-39
        "detail": detail,
    }


async def _window_stats(session: AsyncSession, project_id: str, since: datetime) -> dict[str, Any]:
    """Work items closed over the window: first pass and cost, work item by work item."""
    items = (
        (
            await session.execute(
                select(WorkItem).where(
                    WorkItem.project_id == project_id,
                    WorkItem.closed_at.is_not(None),
                    WorkItem.closed_at >= since,
                )
            )
        )
        .scalars()
        .all()
    )
    if not items:
        return {"tickets": 0, "first_pass_rate": None, "costs": []}
    first_pass = 0
    costs: list[float] = []
    for item in items:
        runs = (await session.execute(select(Run).where(Run.work_item_id == item.id))).scalars().all()
        attempts = [run.attempt for run in runs if run.stage_role == "implement"]
        if not attempts or max(attempts) <= 1:
            first_pass += 1
        costs.append(round(sum(run.cost_usd or 0.0 for run in runs), 6))
    return {"tickets": len(items), "first_pass_rate": round(first_pass / len(items), 4), "costs": costs}


def _ab_verdict(groups: dict[str, Any]) -> tuple[str, str]:
    """The verdict is explicit, including when there is none."""
    with_memory, without_memory = groups["with_memory"], groups["without_memory"]
    if (
        with_memory["tickets"] < MIN_WORK_ITEMS_PER_GROUP
        or without_memory["tickets"] < MIN_WORK_ITEMS_PER_GROUP
    ):
        return (
            "not enough data",
            f"{with_memory['tickets']} work item(s) closed with memory, {without_memory['tickets']} without: "
            f"{MIN_WORK_ITEMS_PER_GROUP} are needed on each side to conclude.",
        )
    rate_with = with_memory["first_pass_merge_rate"]
    rate_without = without_memory["first_pass_merge_rate"]
    cost_with = with_memory["cost_per_ticket_usd"]
    cost_without = without_memory["cost_per_ticket_usd"]
    if rate_with is None or rate_without is None or cost_with is None or cost_without is None:
        return ("not enough data", "one of the two groups has no measurable rate or cost.")
    delta_rate = rate_with - rate_without
    delta_cost = cost_with - cost_without
    detail = (
        f"first pass: {rate_with:.0%} with memory against {rate_without:.0%} without "
        f"({delta_rate:+.1%}) · cost per work item: {cost_with:.2f} $ against {cost_without:.2f} $ "
        f"({delta_cost:+.2f} $)."
    )
    if delta_rate > 0.05 and delta_cost <= 0:
        return ("memory pays off", detail)
    if delta_rate < -0.05 or delta_cost > 0.2 * max(cost_without, 0.01):
        return ("memory does not pay off", detail)
    return ("no clear difference", detail)


MIN_TICKETS_PAR_GROUPE = MIN_WORK_ITEMS_PER_GROUP  # lang: fr-ok alias:internal

alias_names(
    __name__,
    {"MIN_TICKETS_PAR_GROUPE": "MIN_WORK_ITEMS_PER_GROUP"},  # lang: fr-ok alias:internal
)
