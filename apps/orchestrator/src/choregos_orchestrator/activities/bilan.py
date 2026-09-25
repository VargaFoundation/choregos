"""Le bilan d'une étape : la dépense lue au gateway, le résultat consigné, les findings gardés.

Toutes idempotentes par `run_id` : `collect_spend` ne compte qu'une fois (`spend_collected`),
`record_run_outcome` n'écrase pas un résultat déjà posé.
"""

from __future__ import annotations

from typing import Any

from choregos_api.db.models import Finding as FindingRow
from choregos_api.db.models import GatewayKeyRow, Run
from choregos_api.services import persist_event, ranger_les_sorties, record_cost
from choregos_contracts import EventType, StageResult, StageStatus
from choregos_core import Spend, elapsed_seconds, utcnow
from sqlalchemy import select
from temporalio import activity

from ..config import get_settings
from ..train_client import signal_findings
from .base import db, load_work_item, project_bundle


@activity.defn(name="collect_spend")
async def collect_spend(payload: dict[str, Any]) -> dict[str, Any]:
    """Lit la dépense au gateway (source de vérité du coût), l'inscrit au ledger, révoque la clé."""
    settings = get_settings()
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        run = await session.get(Run, payload["run_id"])
        if run is None:
            return Spend().model_dump(mode="json")
        if run.spend_collected:
            return dict(run.tokens)
        spend = Spend()
        if run.gateway_key_id:
            spend = await bundle.adapters.gateway.spend(run.gateway_key_id)
        item = await load_work_item(session, run.work_item_id)
        run.cost_usd = spend.cost_usd
        run.tokens = {
            "tokens_in": spend.tokens_in,
            "tokens_out": spend.tokens_out,
            "tokens_cached": spend.tokens_cached,
            "cost_usd": spend.cost_usd,
            "cost_eur": round(spend.cost_usd * settings.fx_usd_eur, 6),
            "runs": 1,
        }
        run.spend_collected = True
        run.ended_at = run.ended_at or utcnow()
        await record_cost(
            session,
            project_id=bundle.project.id,
            work_item_id=item.id,
            run_id=run.id,
            provider=(spend.models_used[0].split("/")[0] if spend.models_used else ""),
            model=run.model or "",
            backend=run.backend,
            stage_role=run.stage_role,
            size=item.size,
            tokens_in=spend.tokens_in,
            tokens_out=spend.tokens_out,
            tokens_cached=spend.tokens_cached,
            cost_usd=spend.cost_usd,
            fx_rate=settings.fx_usd_eur,
        )
        totals = dict(item.totals or {})
        for field in ("tokens_in", "tokens_out", "tokens_cached"):
            totals[field] = int(totals.get(field, 0)) + int(run.tokens[field])
        totals["cost_usd"] = round(float(totals.get("cost_usd", 0.0)) + spend.cost_usd, 6)
        totals["cost_eur"] = round(float(totals.get("cost_eur", 0.0)) + run.tokens["cost_eur"], 6)
        totals["runs"] = int(totals.get("runs", 0)) + 1
        totals["duration_s"] = float(totals.get("duration_s", 0.0)) + elapsed_seconds(
            run.started_at, run.ended_at
        )
        item.totals = totals

        if run.gateway_key_id:
            await bundle.adapters.gateway.revoke(run.gateway_key_id)
            key_row = (
                await session.execute(select(GatewayKeyRow).where(GatewayKeyRow.key_id == run.gateway_key_id))
            ).scalar_one_or_none()
            if key_row is not None:
                key_row.spend_usd = spend.cost_usd
                key_row.revoked = True
        return dict(run.tokens)


@activity.defn(name="record_run_outcome")
async def record_run_outcome(payload: dict[str, Any]) -> dict[str, Any]:
    """Consigne le résultat d'une étape (statut, findings comptés) sans le rejouer deux fois."""
    async with db() as session:
        run = await session.get(Run, payload["run_id"])
        if run is None:
            return {"recorded": False}
        result = StageResult.model_validate(payload["result"])
        if run.result is None:
            run.result = result.model_dump(mode="json", by_alias=True)
        run.status = "succeeded" if result.status == StageStatus.DONE else "failed"
        run.ended_at = run.ended_at or utcnow()
        item = await load_work_item(session, run.work_item_id)
        outputs = result.outputs
        if outputs.size:
            item.size = str(outputs.size)
        if outputs.risk:
            item.risk = str(outputs.risk)
        if outputs.allowed_paths:
            item.allowed_paths = list(outputs.allowed_paths)
        declarees = list(((run.stage_input or {}).get("transition") or {}).get("outputs") or [])
        documents = ranger_les_sorties(item, outputs, declarees)
        # Une PR d'infra déclarée par l'agent (`artifacts.reports["infra_pr"]`) suit le
        # ticket jusqu'au train, qui en déclenchera l'`apply` Atlantis après approbation.
        infra_pr = result.artifacts.reports.get("infra_pr")
        if infra_pr:
            documents["infra_pr_url"] = infra_pr
        item.documents = documents
        if result.artifacts.pr_url:
            item.pr_url = result.artifacts.pr_url
        recorded = await _persist_findings(session, run, item, result)
        return {"recorded": True, "status": run.status, "findings": recorded}


async def _persist_findings(session: Any, run: Run, item: Any, result: StageResult) -> list[str]:
    """Les findings déclarés dans le résultat ne se perdent pas.

    Le runner les poste normalement un par un pendant le run (outil MCP `report_finding`) ;
    si l'API était indisponible à ce moment, le résultat les porte encore. On les enregistre
    ici, sans doublon (même run, même titre), puis on réveille le triage.
    """
    if not result.findings:
        return []
    bundle = await project_bundle(session, run.project_id)
    cap = bundle.engine.max_findings_per_run()
    existing = (
        (await session.execute(select(FindingRow).where(FindingRow.origin_run_id == run.id))).scalars().all()
    )
    known = {row.title for row in existing}
    created: list[str] = []
    for finding in result.findings:
        if finding.title in known or len(existing) + len(created) >= cap:
            continue
        row = FindingRow(
            project_id=run.project_id,
            origin_work_item_id=item.id,
            origin_run_id=run.id,
            title=finding.title,
            type=str(finding.type),
            severity=str(finding.severity),
            evidence=finding.evidence,
            suggested_fix=finding.suggested_fix,
            estimate=str(finding.estimate) if finding.estimate else None,
            status="pending",
        )
        session.add(row)
        await session.flush()
        created.append(row.id)
        await persist_event(
            session,
            EventType.FINDING_REPORTED,
            project_id=run.project_id,
            work_item_id=item.id,
            project_slug=bundle.slug,
            subject=row.id,
            finding_id=row.id,
            severity=row.severity,
            title=row.title,
        )
    for finding_id in created:
        await signal_findings(
            bundle.slug, "finding", {"finding_id": finding_id, "project_id": run.project_id}
        )
    return created
