"""Preuve avant dépendance : la comparaison A/B des projets avec et sans mémoire."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from choregos_core import (
    utcnow,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    Organization,
    Project,
    Run,
    WorkItem,
)
from .definitions import active_policy, policy_model

MIN_TICKETS_PAR_GROUPE = 10
"""En dessous, on ne conclut pas : dix tickets fermés par bras, au minimum."""


async def memory_ab_comparison(session: AsyncSession, org: str, weeks: int = 4) -> dict[str, Any]:
    """Compare les projets **avec** et **sans** context pack (docs/plan/04, décision 4).

    La mémoire n'est pas un acquis : elle doit payer. On mesure, sur la fenêtre demandée,
    le taux de PR mergée au premier passage et le coût par ticket, groupe contre groupe.
    """
    from statistics import median

    since = utcnow() - timedelta(weeks=weeks)
    buckets: dict[str, dict[str, Any]] = {
        "with_memory": {"projects": [], "rates": [], "costs": [], "tickets": 0},
        "without_memory": {"projects": [], "rates": [], "costs": [], "tickets": 0},
    }
    organization = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organization is not None:
        # Tout projet non archivé compte : ce qui décide de l'échantillon, ce sont les
        # tickets fermés sur la fenêtre, pas le statut administratif du projet.
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
            policy = policy_model(await active_policy(session, project.id))
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
        "verdict": "organisation inconnue" if organization is None else verdict,
        "detail": detail,
    }


async def _window_stats(session: AsyncSession, project_id: str, since: datetime) -> dict[str, Any]:
    """Tickets fermés sur la fenêtre : premier passage et coût, ticket par ticket."""
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
    """Le verdict est explicite, y compris quand il n'y en a pas."""
    avec, sans = groups["with_memory"], groups["without_memory"]
    if avec["tickets"] < MIN_TICKETS_PAR_GROUPE or sans["tickets"] < MIN_TICKETS_PAR_GROUPE:
        return (
            "échantillon insuffisant",
            f"{avec['tickets']} ticket(s) fermé(s) avec mémoire, {sans['tickets']} sans : "
            f"il en faut {MIN_TICKETS_PAR_GROUPE} de chaque côté pour conclure.",
        )
    rate_avec, rate_sans = avec["first_pass_merge_rate"], sans["first_pass_merge_rate"]
    cost_avec, cost_sans = avec["cost_per_ticket_usd"], sans["cost_per_ticket_usd"]
    if rate_avec is None or rate_sans is None or cost_avec is None or cost_sans is None:
        return ("échantillon insuffisant", "un des deux groupes n'a ni taux ni coût mesurable.")
    delta_rate = rate_avec - rate_sans
    delta_cost = cost_avec - cost_sans
    detail = (
        f"premier passage : {rate_avec:.0%} avec mémoire contre {rate_sans:.0%} sans "
        f"({delta_rate:+.1%}) · coût par ticket : {cost_avec:.2f} $ contre {cost_sans:.2f} $ "
        f"({delta_cost:+.2f} $)."
    )
    if delta_rate > 0.05 and delta_cost <= 0:
        return ("la mémoire paie", detail)
    if delta_rate < -0.05 or delta_cost > 0.2 * max(cost_sans, 0.01):
        return ("la mémoire ne paie pas", detail)
    return ("pas de différence nette", detail)
