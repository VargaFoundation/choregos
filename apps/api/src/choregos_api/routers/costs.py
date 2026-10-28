# SPDX-License-Identifier: Apache-2.0
"""Coûts : agrégats par jour, étape, modèle, backend, taille, export CSV — et métriques DORA."""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Annotated, Any

from choregos_contracts import StageRole
from choregos_core import aware, utcnow
from fastapi import APIRouter, Query, Response
from sqlalchemy import func, or_, select

from ..db.models import CostLedger, Deployment, Finding, Organization, Project, Release, Run, WorkItem
from ..deps import Db, Me, ProjectCtx
from ..errors import forbidden, not_found
from ..rbac import Permission
from ..schemas import (
    CostReport,
    CostRow,
    CostTotal,
    CrossBackendArm,
    CrossBackendReport,
    DoraMetric,
    DoraReport,
)
from ..services import effective_policy

router = APIRouter(tags=["costs"])

GROUPS: dict[str, Any] = {
    # `kind` sépare ce qu'a coûté un MODÈLE de ce qu'a coûté un OUTIL du catalogue. Sans
    # lui, une facture d'API tierce se fondait dans le coût des modèles et n'était donc
    # visible nulle part — la seule dépense de la plateforme que personne ne regardait.
    "kind": CostLedger.kind,
    "stage": CostLedger.stage_role,
    "model": CostLedger.model,
    "backend": CostLedger.backend,
    "size": CostLedger.size,
    "project": CostLedger.project_id,
    # Who spent it (S29-01, S29-02).
    "agent": CostLedger.agent_slug,
    "workflow": CostLedger.workflow_name,
    "work_item": CostLedger.work_item_id,
}
#: Every grouping, at project and organisation scope alike, and in the CSV.
GROUP_PATTERN = "^(day|stage|model|backend|size|kind|agent|workflow|work_item|project)$"


def _default_since() -> datetime:
    return utcnow() - timedelta(days=30)


async def _report(
    session: Db,
    *,
    where: list[Any],
    group_by: str,
    budget_usd: float | None = None,
) -> CostReport:
    column: Any = GROUPS.get(group_by, CostLedger.project_id)
    if group_by == "day":
        column = func.date(CostLedger.ts)
    query = (
        select(
            column.label("key"),
            func.sum(CostLedger.cost_usd),
            func.sum(CostLedger.cost_eur),
            func.sum(CostLedger.tokens_in),
            func.sum(CostLedger.tokens_out),
            func.sum(CostLedger.tokens_cached),
            func.count(CostLedger.id),
        )
        .where(*where)
        .group_by(column)
        .order_by(column)
    )
    rows = (await session.execute(query)).all()
    report_rows = [
        CostRow(
            key=str(row[0] if row[0] is not None else "—"),
            cost_usd=round(float(row[1] or 0), 6),
            cost_eur=round(float(row[2] or 0), 6),
            tokens_in=int(row[3] or 0),
            tokens_out=int(row[4] or 0),
            tokens_cached=int(row[5] or 0),
            runs=int(row[6] or 0),
        )
        for row in rows
    ]
    return CostReport(
        group_by=group_by,
        rows=report_rows,
        total=CostTotal(
            cost_usd=round(sum(r.cost_usd for r in report_rows), 6),
            cost_eur=round(sum(r.cost_eur for r in report_rows), 6),
            budget_usd=budget_usd,
        ),
    )


def _period(since: date | None, until: date | None, kind: str | None = None) -> list[Any]:
    """The ledger lines of a period: `since` (default 30 days ago) to `until`, both inclusive;
    `kind` keeps only what models cost, or only what tools cost (S29-03)."""
    where: list[Any] = [
        CostLedger.ts >= (datetime.combine(since, datetime.min.time()) if since else _default_since())
    ]
    if until:
        where.append(CostLedger.ts <= datetime.combine(until, datetime.max.time()))
    if kind:
        where.append(CostLedger.kind == kind)
    return where


async def _readable_keys(session: Db, report: CostReport, org_id: str | None) -> CostReport:
    """A project is named by its slug and a work item by its tracker key, not by their identifiers."""
    if report.group_by == "project":
        query = select(Project.id, Project.slug)
        if org_id is not None:
            query = query.where(Project.org_id == org_id)
        names = {str(r[0]): str(r[1]) for r in (await session.execute(query)).all()}
    elif report.group_by == "work_item":
        ids = [r.key for r in report.rows if r.key != "—"]
        rows = (
            await session.execute(select(WorkItem.id, WorkItem.tracker_key).where(WorkItem.id.in_(ids)))
        ).all()
        names = {str(r[0]): str(r[1]) for r in rows}
    else:
        return report
    for row in report.rows:
        row.key = names.get(row.key, row.key)
    return report


@router.get("/projects/{id}/costs", response_model=CostReport, operation_id="getProjectCosts")
async def project_costs(
    ctx: ProjectCtx,
    session: Db,
    group_by: Annotated[str, Query(pattern=GROUP_PATTERN)] = "day",
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
    kind: Annotated[str | None, Query(pattern="^(model|tool|distill)$")] = None,
) -> CostReport:
    where: list[Any] = [CostLedger.project_id == ctx.id, *_period(since, until, kind)]
    policy = await effective_policy(session, ctx.id)
    report = await _report(
        session, where=where, group_by=group_by, budget_usd=policy.budgets.daily_project_usd
    )
    organisation = await session.get(Organization, ctx.project.org_id)
    if organisation is not None and (organisation.settings or {}).get("currency") == "USD":
        report.currency = "USD"
    return await _readable_keys(session, report, None)


@router.get("/orgs/{org}/costs", response_model=CostReport, operation_id="getOrgCosts")
async def org_costs(
    org: str,
    session: Db,
    principal: Me,
    group_by: Annotated[str, Query(pattern=GROUP_PATTERN)] = "day",
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
    kind: Annotated[str | None, Query(pattern="^(model|tool|distill)$")] = None,
) -> CostReport:
    organization = (
        await session.execute(select(Organization).where(Organization.slug == org))
    ).scalar_one_or_none()
    if organization is None:
        raise not_found("Organisation", org)
    if not principal.can(Permission.PROJECT_READ, org):
        raise forbidden()
    project_ids = (
        (await session.execute(select(Project.id).where(Project.org_id == organization.id))).scalars().all()
    )
    # The organisation's projects' lines, and its own (a distillation of a base with no owning
    # project, ADR 0092): the lines with no project are only ever visible here.
    where: list[Any] = [
        or_(CostLedger.project_id.in_(list(project_ids) or [""]), CostLedger.org_id == organization.id),
        *_period(since, until, kind),
    ]
    report = await _report(session, where=where, group_by=group_by)
    report.currency = "USD" if (organization.settings or {}).get("currency") == "USD" else "EUR"
    # The organisation's daily budget: what its projects may spend a day, summed (S29-03).
    daily = [(await effective_policy(session, pid)).budgets.daily_project_usd for pid in project_ids]
    budgets = [b for b in daily if b is not None]
    report.total.budget_usd = round(sum(budgets), 6) if budgets else None
    return await _readable_keys(session, report, organization.id)


@router.get(
    "/projects/{id}/costs.csv",
    operation_id="exportProjectCostsCsv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "Cost report as CSV"}},
)
async def project_costs_csv(
    ctx: ProjectCtx,
    session: Db,
    group_by: Annotated[str, Query(pattern=GROUP_PATTERN)] = "day",
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
    kind: Annotated[str | None, Query(pattern="^(model|tool|distill)$")] = None,
) -> Response:
    """Le même rapport, en CSV : de quoi le recoller dans un tableur ou une facturation."""
    report = await project_costs(ctx, session, group_by=group_by, since=since, until=until, kind=kind)
    return _csv_response(report, f"couts-{ctx.slug}-{group_by}-{utcnow():%Y%m%d}.csv")


@router.get(
    "/orgs/{org}/costs.csv",
    operation_id="exportOrgCostsCsv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "Organisation cost report as CSV"}},
)
async def org_costs_csv(
    org: str,
    session: Db,
    principal: Me,
    group_by: Annotated[str, Query(pattern=GROUP_PATTERN)] = "day",
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
    kind: Annotated[str | None, Query(pattern="^(model|tool|distill)$")] = None,
) -> Response:
    """The organisation's report, in the grouping the screen shows."""
    report = await org_costs(org, session, principal, group_by=group_by, since=since, until=until, kind=kind)
    return _csv_response(report, f"couts-{org}-{group_by}-{utcnow():%Y%m%d}.csv")


def _csv_response(report: CostReport, filename: str) -> Response:
    group_by = report.group_by
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";")
    writer.writerow([group_by, "cout_usd", "cout_eur", "tokens_in", "tokens_out", "tokens_caches", "runs"])
    for row in report.rows:
        writer.writerow(
            [
                row.key,
                f"{row.cost_usd:.6f}",
                f"{row.cost_eur:.6f}",
                row.tokens_in,
                row.tokens_out,
                row.tokens_cached,
                row.runs,
            ]
        )
    writer.writerow([])
    writer.writerow(["total", f"{report.total.cost_usd:.6f}", f"{report.total.cost_eur:.6f}"])
    if report.total.budget_usd is not None:
        writer.writerow(["budget_quotidien_usd", f"{report.total.budget_usd:.2f}"])
    return Response(
        # Excel francophone lit le point-virgule ; le BOM lui évite de casser les accents.
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ───────────────────────── métriques de livraison (DORA) ─────────────────────────

FREQUENCY_LEVELS = ((1.0, "elite"), (1 / 7, "high"), (1 / 30, "medium"))
LEAD_TIME_LEVELS = ((24.0, "elite"), (24.0 * 7, "high"), (24.0 * 30, "medium"))
RESTORE_LEVELS = ((1.0, "elite"), (24.0, "high"), (24.0 * 7, "medium"))
FAILURE_LEVELS = ((0.05, "elite"), (0.10, "high"), (0.15, "medium"))


def _level(value: float | None, thresholds: tuple[tuple[float, str], ...], *, higher_is_better: bool) -> str:
    if value is None:
        return "unknown"
    for limit, name in thresholds:
        if (value >= limit) if higher_is_better else (value <= limit):
            return name
    return "low"


def _hours(start: datetime | None, end: datetime | None) -> float | None:
    """Écart en heures, robuste aux horodatages naïfs rendus par SQLite."""
    if start is None or end is None:
        return None
    from choregos_core import elapsed_seconds

    delta = elapsed_seconds(start, end) / 3600
    return delta if delta >= 0 else None


@router.get("/projects/{id}/metrics/dora", response_model=DoraReport, operation_id="getProjectDora")
async def project_dora(
    ctx: ProjectCtx,
    session: Db,
    env: Annotated[str, Query(pattern="^[a-z0-9-]{1,32}$")] = "prod",
    since: Annotated[date | None, Query()] = None,
    until: Annotated[date | None, Query()] = None,
) -> DoraReport:
    """Les quatre mesures DORA, dérivées des déploiements et des rollbacks enregistrés.

    Rien n'est déclaré à la main : la fréquence vient des lignes `deployments` clôturées,
    le délai de livraison de l'écart entre la fusion d'un ticket et la mise en production
    du lot qui le portait, le taux d'échec des rollbacks, et le délai de rétablissement
    de l'écart entre un rollback et le déploiement réussi qui l'a suivi.
    """
    # Les deux bornes sont conscientes du fuseau : SQLite rend des horodatages naïfs et
    # les soustraire à une borne aware lèverait un TypeError en pleine requête.
    start = datetime.combine(since, datetime.min.time(), tzinfo=UTC) if since else _default_since()
    end = datetime.combine(until, datetime.max.time(), tzinfo=UTC) if until else utcnow()
    rows = (
        (
            await session.execute(
                select(Deployment, Release)
                .join(Release, Deployment.release_id == Release.id)
                .where(
                    Release.project_id == ctx.id,
                    Deployment.env == env,
                    Deployment.started_at >= start,
                    Deployment.started_at <= end,
                )
                .order_by(Deployment.started_at)
            )
        )
        .tuples()
        .all()
    )
    succeeded = [(d, r) for d, r in rows if d.status == "succeeded"]
    failed = [(d, r) for d, r in rows if d.status == "rolled_back"]
    finished = succeeded + failed

    days = max(1.0, ((end - start).total_seconds() / 86400))
    frequency = len(succeeded) / days

    lead_times: list[float] = []
    for deployment, release in succeeded:
        for item in release.items or []:
            merged_at = item.get("merged_at")
            if not merged_at:
                continue
            hours = _hours(datetime.fromisoformat(str(merged_at)), deployment.ended_at)
            if hours is not None:
                lead_times.append(hours)

    restores: list[float] = []
    ordered = sorted(finished, key=lambda pair: aware(pair[0].started_at) or start)
    for index, (deployment, _release) in enumerate(ordered):
        if deployment.status != "rolled_back":
            continue
        following = next((d for d, _ in ordered[index + 1 :] if d.status == "succeeded"), None)
        hours = _hours(deployment.ended_at, following.ended_at) if following else None
        if hours is not None:
            restores.append(hours)

    failure_rate = (len(failed) / len(finished)) if finished else None
    lead_p50 = median(lead_times) if lead_times else None
    restore_p50 = median(restores) if restores else None
    return DoraReport(
        env=env,
        since=start,
        until=end,
        deployments=len(succeeded),
        deployment_frequency=DoraMetric(
            value=round(frequency, 4),
            unit="par jour",
            level=_level(frequency, FREQUENCY_LEVELS, higher_is_better=True) if succeeded else "unknown",
            sample=len(succeeded),
        ),
        lead_time=DoraMetric(
            value=round(lead_p50, 2) if lead_p50 is not None else None,
            unit="hours (median)",
            level=_level(lead_p50, LEAD_TIME_LEVELS, higher_is_better=False),
            sample=len(lead_times),
        ),
        change_failure_rate=DoraMetric(
            value=round(failure_rate, 4) if failure_rate is not None else None,
            unit="share of production releases",
            level=_level(failure_rate, FAILURE_LEVELS, higher_is_better=False),
            sample=len(finished),
        ),
        time_to_restore=DoraMetric(
            value=round(restore_p50, 2) if restore_p50 is not None else None,
            unit="hours (median)",
            level=_level(restore_p50, RESTORE_LEVELS, higher_is_better=False),
            sample=len(restores),
        ),
    )


# ───────────────────── revue croisée : le multi-backend paie-t-il ? ─────────────────────

MIN_REVIEWS_PAR_BRAS = 5


@router.get(
    "/projects/{id}/metrics/cross-backend",
    response_model=CrossBackendReport,
    operation_id="getProjectCrossBackend",
)
async def project_cross_backend(
    ctx: ProjectCtx,
    session: Db,
    since: Annotated[date | None, Query()] = None,
) -> CrossBackendReport:
    """Compare les revues faites par un autre backend à celles faites par le même (S13-03).

    « Un relecteur qui n'est pas l'implémenteur » est une politique ; savoir si elle sert à
    quelque chose demande une mesure. On compte, par bras, les revues qui ont trouvé
    quelque chose — un finding déposé, ou un résultat autre que `done`.
    """
    start = datetime.combine(since, datetime.min.time(), tzinfo=UTC) if since else _default_since()
    runs = (
        (
            await session.execute(
                select(Run).where(
                    Run.project_id == ctx.id,
                    Run.stage_role.in_([str(StageRole.REVIEW), str(StageRole.VERIFY)]),
                    Run.created_at >= start,
                )
            )
        )
        .scalars()
        .all()
    )
    implementers: dict[str, str] = {}
    for row in (
        (
            await session.execute(
                select(Run.work_item_id, Run.backend)
                .where(
                    Run.project_id == ctx.id,
                    Run.stage_role == str(StageRole.IMPLEMENT),
                    Run.backend.is_not(None),
                )
                .order_by(Run.created_at)
            )
        )
        .tuples()
        .all()
    ):
        implementers[str(row[0])] = str(row[1])

    with_findings = set(
        (
            await session.execute(
                select(Finding.origin_run_id).where(
                    Finding.project_id == ctx.id, Finding.origin_run_id.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )

    arms: dict[str, dict[str, Any]] = {
        "same": {"reviews": 0, "caught": 0, "backends": set()},
        "other": {"reviews": 0, "caught": 0, "backends": set()},
    }
    for run in runs:
        implementer = implementers.get(run.work_item_id)
        if not implementer or not run.backend:
            continue  # sans implémenteur connu, la comparaison n'a pas de sens
        arm = arms["same" if run.backend == implementer else "other"]
        arm["reviews"] += 1
        arm["backends"].add(run.backend)
        caught = run.id in with_findings or (run.result or {}).get("status") not in {None, "done"}
        if caught:
            arm["caught"] += 1

    def to_arm(raw: dict[str, Any]) -> CrossBackendArm:
        rate = round(raw["caught"] / raw["reviews"], 4) if raw["reviews"] else None
        return CrossBackendArm(
            reviews=raw["reviews"],
            caught=raw["caught"],
            catch_rate=rate,
            backends=sorted(raw["backends"]),
        )

    same, other = to_arm(arms["same"]), to_arm(arms["other"])
    policy = await effective_policy(session, ctx.id)
    verdict, detail = _cross_backend_verdict(same, other)
    return CrossBackendReport(
        since=start,
        cross_backend_required=policy.review.cross_backend,
        same_backend=same,
        other_backend=other,
        verdict=verdict,
        detail=detail,
    )


def _cross_backend_verdict(same: CrossBackendArm, other: CrossBackendArm) -> tuple[str, str]:
    if same.reviews < MIN_REVIEWS_PAR_BRAS or other.reviews < MIN_REVIEWS_PAR_BRAS:
        return (
            "not enough data",
            f"{other.reviews} review(s) by another backend, {same.reviews} by the same one: "
            f"{MIN_REVIEWS_PAR_BRAS} are needed on each side to compare.",
        )
    if same.catch_rate is None or other.catch_rate is None:
        return ("not enough data", "one of the two arms has no measurable rate.")
    delta = other.catch_rate - same.catch_rate
    detail = (
        f"another backend finds something in {other.catch_rate:.0%} of the reviews, "
        f"the same one in {same.catch_rate:.0%} ({delta:+.1%})."
    )
    if delta > 0.05:
        return ("cross review catches more", detail)
    if delta < -0.05:
        return ("cross review catches less", detail)
    return ("no clear difference", detail)
