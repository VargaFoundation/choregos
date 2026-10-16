# SPDX-License-Identifier: Apache-2.0
"""Le bilan d'une étape : la dépense lue au gateway, le résultat consigné, les findings gardés.

Toutes idempotentes par `run_id` : `collect_spend` ne compte qu'une fois (`spend_collected`),
`record_run_outcome` n'écrase pas un résultat déjà posé.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

from choregos_api.db.models import Finding as FindingRow
from choregos_api.db.models import Project, Run
from choregos_api.logging import get_logger
from choregos_api.services import persist_event, ranger_les_sorties, record_cost
from choregos_api.services.artifacts import record_inline_outputs
from choregos_api.services.budget_alerts import unsent_month_alerts
from choregos_api.services.budgets import ticket_budget, ticket_spent
from choregos_api.services.model_catalogue import priced_spend
from choregos_api.services.tickets import sorties_a_ranger
from choregos_contracts import EventType, StageInput, StageResult, StageStatus
from choregos_core import Message, Spend, elapsed_seconds, utcnow
from sqlalchemy import select
from temporalio import activity

from ..config import get_settings
from ..train_client import signal_findings
from .base import db, load_work_item, project_bundle
from .remote import declared_cost_usd
from .run_key import release_run_key

logger = get_logger("choregos.bilan")


async def _tell_slack_of_agent_alerts(session: Any, bundle: Any) -> None:
    """The monthly alerts of the organisation's agents (50, 80, 100 %) that Slack has not heard yet.
    One message each; `notified_at` is what makes it once. A notifier that is absent or down leaves
    the alert unsent: the next stage end tries again, and the inbox has it meanwhile."""
    for alert in await unsent_month_alerts(session, bundle.project.org_id):
        full = alert.threshold >= 100
        try:
            await bundle.adapters.notify.send(
                bundle.config.notify.slack_channel or "#choregos",
                Message(
                    title=(
                        f"Agent {alert.subject}: monthly budget {alert.threshold} % "
                        f"({alert.spent_usd:.2f} / {alert.budget_usd:.2f} $)"
                    ),
                    body=(
                        "The agent is suspended: its new runs are refused until it is resumed."
                        if full
                        else "Nothing is stopped yet."
                    ),
                    severity="error" if full else "warning",
                    context={"agent": alert.subject, "month": alert.period_key},
                ),
            )
        except Exception as error:
            activity.logger.warning("agent budget alert not sent: %s", error)
            return
        alert.notified_at = utcnow()


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
            return {**run.tokens, **await _ticket_view(session, bundle, run.work_item_id)}
        spend = Spend()
        if run.gateway_key_id:
            spend = await bundle.adapters.gateway.spend(run.gateway_key_id)
        # The organisation's price override (ADR 0060) replaces the gateway's figure when tokens are
        # known; a gateway that cannot read a spend back leaves the line `unmeasured`.
        spend, pricing = await priced_spend(
            session,
            bundle.project.org_id,
            [run.model or "", *spend.models_used],
            spend,
            measured="spend_readback" in getattr(bundle.adapters.gateway, "capabilities", {"spend_readback"}),
        )
        # A remote agent reports no cost (ADR 0062): what its registry entry DECLARES is booked,
        # as `external`, apart from the model spend.
        remote = StageInput.model_validate(run.stage_input).remote if run.stage_input else None
        if remote is not None:
            wall = elapsed_seconds(run.started_at, run.ended_at or utcnow())
            spend = Spend(cost_usd=declared_cost_usd(StageInput.model_validate(run.stage_input), wall))
        item = await load_work_item(session, run.work_item_id)
        segment = {
            "tokens_in": spend.tokens_in,
            "tokens_out": spend.tokens_out,
            "tokens_cached": spend.tokens_cached,
            "cost_usd": spend.cost_usd,
            "cost_eur": round(spend.cost_usd * settings.fx_usd_eur, 6),
        }
        # A run resumed after a person's answer (S35-03) is ONE run in two segments: what it
        # spent before the question stays on it, the continuation is added, and the work item
        # does not count a second run.
        resumed = dict(run.tokens or {}) if (run.tokens or {}).get("resumed") else {}
        of_the_run: dict[str, Any] = dict(resumed)
        for name in ("tokens_in", "tokens_out", "tokens_cached"):
            of_the_run[name] = int(resumed.get(name, 0)) + int(segment[name])
        for name in ("cost_usd", "cost_eur"):
            of_the_run[name] = round(float(resumed.get(name, 0.0)) + float(segment[name]), 6)
        of_the_run["runs"] = 1
        of_the_run.update(pricing)
        run.tokens = of_the_run
        run.cost_usd = float(of_the_run["cost_usd"])
        run.spend_collected = True
        run.ended_at = run.ended_at or utcnow()
        await record_cost(
            session,
            project_id=bundle.project.id,
            work_item_id=item.id,
            run_id=run.id,
            provider=(
                (urlsplit(remote.endpoint).hostname or "")
                if remote is not None
                else (spend.models_used[0].split("/")[0] if spend.models_used else "")
            ),
            model=(run.model or "") + (" (unmeasured)" if remote is not None and spend.cost_usd == 0 else ""),
            kind="external" if remote is not None else "model",
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
            totals[field] = int(totals.get(field, 0)) + int(segment[field])
        totals["cost_usd"] = round(float(totals.get("cost_usd", 0.0)) + spend.cost_usd, 6)
        totals["cost_eur"] = round(float(totals.get("cost_eur", 0.0)) + segment["cost_eur"], 6)
        totals["runs"] = int(totals.get("runs", 0)) + (0 if resumed else 1)
        totals["duration_s"] = float(totals.get("duration_s", 0.0)) + elapsed_seconds(
            run.started_at, run.ended_at
        )
        item.totals = totals

        # The run is over: its key is revoked and no copy of it stays on the row (#519, #523).
        await release_run_key(session, bundle, run, spend_usd=spend.cost_usd)
        await session.flush()
        await _tell_slack_of_agent_alerts(session, bundle)
        # What the ticket has cost, read from the ledger — this run's line included, tool calls
        # too (S27-03): the interpreter's counter is this, not a sum it keeps in memory.
        return {**run.tokens, **await _ticket_view(session, bundle, item.id)}


async def close_run_spend(project_id: str, run_id: str) -> None:
    """A run that ends without the interpreter's `collect_spend` (cancelled, its workflow dead):
    its spend is booked and its key revoked all the same (#523). Never raises.

    `collect_spend` first, because a revoked key may no longer tell its spend; if the gateway
    cannot answer, the key is still revoked and scrubbed, and its spend is not booked.
    """
    try:
        await collect_spend({"project_id": project_id, "run_id": run_id})
        return
    except Exception as error:
        logger.warning("spend of an abandoned run not collected", run_id=run_id, error=str(error)[:300])
    try:
        async with db() as session:
            bundle = await project_bundle(session, project_id)
            run = await session.get(Run, run_id)
            if run is not None:
                await release_run_key(session, bundle, run)
    except Exception as error:  # a cleanup: the key still dies at its TTL
        logger.warning("key of an abandoned run not released", run_id=run_id, error=str(error)[:300])


async def _ticket_view(session: Any, bundle: Any, work_item_id: str) -> dict[str, float]:
    """What the ticket has cost, read from the ledger (models and tools), and its budget as it
    stands now — its size may have changed since the interpreter started (S27-03)."""
    item = await load_work_item(session, work_item_id)
    return {
        "ticket_cost_usd": await ticket_spent(session, work_item_id),
        "ticket_budget_usd": ticket_budget(bundle.engine, item),
    }


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
        if result.status == StageStatus.DONE:
            documents.pop("human_answer", None)  # la réponse a servi à l'étape qui la demandait
        item.documents = documents
        # The same outputs as typed artifacts (ADR 0049). Usually already recorded when the runner
        # posted its result; the same content makes no new version, so this is a no-op then.
        # After `documents` is final: recording flushes, and a later in-place change of the same
        # dict would no longer be seen.
        project = await session.get(Project, run.project_id)
        if project is not None:
            await record_inline_outputs(
                session,
                org_id=project.org_id,
                item=item,
                run=run,
                outputs=sorties_a_ranger(outputs, declarees),
                types=dict(((run.stage_input or {}).get("transition") or {}).get("output_types") or {}),
            )
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
