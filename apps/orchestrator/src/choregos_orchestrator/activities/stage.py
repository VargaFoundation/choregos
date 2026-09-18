"""Activités d'une étape agent : modèle, clé, contexte, lancement, attente, coût, journal.

Chaque fonction est une activité Temporal ; toutes sont idempotentes par `run_id`.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
from typing import Any

from choregos_api.db.models import GatewayKeyRow, Run
from choregos_api.security import mint_run_token
from choregos_api.services import persist_event, record_cost
from choregos_contracts import (
    AgentRef,
    Budget,
    Callbacks,
    ContextPack,
    EventType,
    LaunchSpec,
    McpServerRef,
    Permissions,
    PlaybookRef,
    ProjectRef,
    RepoRef,
    StageInput,
    StageResult,
    StageStatus,
    ToolsRef,
    TransitionRef,
    WorkItemLinks,
    WorkItemRef,
)
from choregos_core import ExecRef, ModelResolver, Spend, StageJobSpec, elapsed_seconds, utcnow
from temporalio import activity

from ..config import get_settings
from ..train_client import signal_findings
from .base import db, load_work_item, project_bundle


@dataclass
class StagePlan:
    """Ce que l'interpréteur transmet aux activités pour préparer un run."""

    project_id: str
    work_item_id: str
    transition_id: str
    role: str
    from_state: str
    to_state: str
    actor: str
    attempt: int
    backend: str | None = None
    model_request: str = "profile:by_size"
    fresh_context: bool = False
    max_turns: int | None = None
    max_minutes: int | None = None
    playbook: str | None = None
    outputs: list[str] | None = None
    inputs: list[str] | None = None


@activity.defn(name="prepare_stage")
async def prepare_stage(plan_data: dict[str, Any]) -> dict[str, Any]:
    """Résout le modèle, mint la clé gateway, construit le context pack et le `StageInput`.

    Une seule activité pour tout le travail préparatoire : le `run_id` la rend rejouable,
    et la clé virtuelle n'est mintée qu'une fois par run.
    """
    plan = StagePlan(**plan_data)
    settings = get_settings()
    async with db() as session:
        bundle = await project_bundle(session, plan.project_id)
        item = await load_work_item(session, plan.work_item_id)
        run_id = _run_id(plan)

        existing = await session.get(Run, run_id)
        if existing is not None and existing.stage_input:
            return {"run_id": run_id, "stage_input": existing.stage_input, "reused": True}

        engine = bundle.engine
        resolver = ModelResolver(gateway_url=settings.gateway_url)
        backend = plan.backend or bundle.config.agent.default_backend
        resolved = resolver.resolve(
            plan.model_request, project=bundle.config, size=item.size, backend=backend
        )
        budget = engine.budget_for(plan.role, item.size, turns_factor=resolved.turns_factor)
        if plan.max_turns:
            budget = Budget(usd=budget.usd, max_turns=plan.max_turns, max_minutes=budget.max_minutes)
        if plan.max_minutes:
            budget = Budget(usd=budget.usd, max_turns=budget.max_turns, max_minutes=plan.max_minutes)

        key = await bundle.adapters.gateway.mint_key(
            {
                "project": bundle.slug,
                "work_item": item.tracker_key,
                "run_id": run_id,
                "transition": plan.transition_id,
                "attempt": plan.attempt,
                "actor": plan.actor,
                "backend": backend,
            },
            budget_usd=budget.usd,
            ttl_s=(budget.max_minutes + 30) * 60,
            models=[resolved.litellm_model],
        )
        session.add(
            GatewayKeyRow(
                key_id=key.key_id,
                run_id=run_id,
                project_id=bundle.project.id,
                budget_usd=budget.usd,
                expires_at=key.expires_at,
            )
        )

        context_pack = await _context_pack(bundle, item, plan)
        playbook_prompt = _render_playbook(plan, bundle, item, context_pack)
        digest = sha256(playbook_prompt.encode()).hexdigest()[:12]
        playbook_ref = f"{plan.playbook or plan.role}@sha256:{digest}"
        allowed_paths = list(item.allowed_paths or []) or ["**"]
        token = mint_run_token(
            run_id,
            project_slug=bundle.slug,
            work_item_key=item.tracker_key,
            ttl_minutes=budget.max_minutes + 15,
        )
        documents = item.documents or {}
        stage_input = StageInput(
            run_id=run_id,
            attempt=plan.attempt,
            project=ProjectRef(
                slug=bundle.slug,
                org=bundle.org_slug,
                test_command=bundle.config.repo.test_command,
                lint_command=bundle.config.repo.lint_command,
                typecheck_command=bundle.config.repo.typecheck_command,
            ),
            work_item=WorkItemRef(
                key=item.tracker_key,
                title=item.title,
                body=item.body_snapshot or "",
                url=item.url,
                size=item.size,
                risk=item.risk,
                links=WorkItemLinks(
                    spec_comment=documents.get("spec_comment"),
                    plan_comment=documents.get("plan_comment"),
                    pr=item.pr_url,
                ),
            ),
            transition=TransitionRef(
                id=plan.transition_id,
                role=plan.role,
                **{"from": plan.from_state},
                to=plan.to_state,
                outputs=plan.outputs or [],
                inputs=plan.inputs or [],
            ),
            repo=RepoRef(
                url=bundle.config.repo.url,
                base_branch=bundle.config.repo.default_branch,
                work_branch=bundle.config.branch_for(item.tracker_key),
                clone_depth=bundle.config.repo.clone_depth,
            ),
            agent=AgentRef(backend=backend, launch=LaunchSpec()),
            model=resolved.to_ref(),
            gateway_key=key.key,
            budget=budget,
            allowed_paths=allowed_paths,
            context_pack_url=f"{settings.object_store_url}/runs/{run_id}/context.json",
            playbook=PlaybookRef(ref=playbook_ref, prompt=playbook_prompt),
            tools=ToolsRef(
                mcp={
                    "choregos": McpServerRef(url="http://localhost:7777/mcp"),
                    "memory": McpServerRef(url=f"{settings.gateway_url.replace('4000', '8432')}/mcp"),
                }
            ),
            permissions=Permissions(
                write_paths=allowed_paths,
                deny_commands=engine.deny_commands(),
                allow_domains=engine.allow_domains(),
                dod_iterations=engine.dod_iterations(),
                max_findings=engine.max_findings_per_run(),
            ),
            callbacks=Callbacks(api_url=f"{settings.api_url}/api/v1/internal", run_token=token),
        )

        payload = stage_input.model_dump(mode="json", by_alias=True)
        run = existing or Run(id=run_id, work_item_id=item.id, project_id=bundle.project.id)
        run.transition_id = plan.transition_id
        run.stage_role = plan.role
        run.attempt = plan.attempt
        run.actor = plan.actor
        run.backend = backend
        run.model = resolved.litellm_model
        run.status = "queued"
        run.stage_input = payload
        run.gateway_key_id = key.key_id
        run.context_pack = context_pack.model_dump(mode="json", by_alias=True)
        run.context_pack_url = stage_input.context_pack_url
        run.playbook_checksum = stage_input.playbook.ref
        run.allowed_paths = allowed_paths
        run.started_at = utcnow()
        if existing is None:
            session.add(run)
        await persist_event(
            session,
            EventType.RUN_QUEUED,
            project_id=bundle.project.id,
            work_item_id=item.id,
            project_slug=bundle.slug,
            subject=run_id,
            run_id=run_id,
            stage=plan.role,
            backend=backend,
            model=resolved.litellm_model,
        )
        return {"run_id": run_id, "stage_input": payload, "reused": False}


def _run_id(plan: StagePlan) -> str:
    """Identifiant déterministe : rejouer l'activité ne crée pas un second run."""
    return f"{plan.work_item_id}-{plan.transition_id}-{plan.attempt}"


async def _context_pack(bundle: Any, item: Any, plan: StagePlan) -> ContextPack:
    """Mémoire bornée par rôle ; une panne rend un pack vide plutôt que de bloquer l'étape."""
    engine = bundle.engine
    budget_tokens = engine.memory_budget(plan.role)
    if budget_tokens <= 0:
        return ContextPack.empty(item.title)
    kinds_by_role = {
        "refine": ["decision", "incident", "ticket_summary"],
        "plan": ["decision", "convention"],
        "implement": ["convention", "run_lesson", "hotspot"],
        "review": ["incident", "hotspot", "decision"],
        "verify": ["flaky_test", "run_lesson"],
    }
    query = f"{item.title}\n{(item.documents or {}).get('spec_markdown', '')}"[:2000]
    try:
        return await asyncio.wait_for(
            bundle.adapters.memory.context_pack(
                bundle.slug,
                query,
                list(item.allowed_paths or []),
                budget_tokens,
                kinds_by_role.get(plan.role),
            ),
            timeout=bundle.policy.memory.read_timeout_ms / 1000 * 10,
        )
    except (TimeoutError, Exception):
        return ContextPack.empty(query)


def _render_playbook(plan: StagePlan, bundle: Any, item: Any, context: ContextPack) -> str:
    from choregos_playbooks import render_playbook

    return render_playbook(
        plan.playbook or plan.role,
        ticket={"key": item.tracker_key, "title": item.title, "body": item.body_snapshot or ""},
        spec=(item.documents or {}).get("spec_markdown", ""),
        plan_markdown=(item.documents or {}).get("plan_markdown", ""),
        allowed_paths=list(item.allowed_paths or []),
        context=context,
        project=bundle.config,
    )


@activity.defn(name="start_run")
async def start_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Démarre l'exécution (Tekton, Job K8s, Docker local, fake) — idempotent par `run_id`."""
    settings = get_settings()
    stage_input = StageInput.model_validate(payload["stage_input"])
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        run = await session.get(Run, stage_input.run_id)
        if run is not None and run.executor_ref:
            return {"executor_ref": run.executor_ref, "kind": run.executor_kind, "reused": True}
        spec = StageJobSpec(
            run_id=stage_input.run_id,
            project_slug=bundle.slug,
            namespace=settings.runner_namespace_pattern.format(slug=bundle.slug),
            runner_image=settings.runner_image,
            api_url=stage_input.callbacks.api_url,
            run_token=stage_input.callbacks.run_token,
            stage_input=stage_input,
            timeout_minutes=stage_input.budget.max_minutes + 10,
            runtime_class="gvisor" if bundle.policy.sandbox.runtime == "gvisor" else None,
            labels={
                "choregos/project": bundle.slug,
                "choregos/run-id": stage_input.run_id,
                "choregos/work-item": stage_input.work_item.key.replace("/", "_").replace("#", "-"),
            },
        )
        ref = await bundle.adapters.executor.start(spec)
        if run is not None:
            run.executor_kind = str(ref.kind)
            run.executor_ref = ref.name
            run.status = "running"
        await persist_event(
            session,
            EventType.RUN_STARTED,
            project_id=bundle.project.id,
            work_item_id=payload["work_item_id"],
            project_slug=bundle.slug,
            subject=stage_input.run_id,
            run_id=stage_input.run_id,
            executor=str(ref.kind),
        )
        return {"executor_ref": ref.name, "kind": str(ref.kind), "namespace": ref.namespace, "reused": False}


@activity.defn(name="await_run")
async def await_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Attend la fin du run, avec heartbeat et annulation propre (S1-03)."""
    settings = get_settings()
    run_id = payload["run_id"]
    timeout_minutes = int(payload.get("timeout_minutes", 120))
    deadline = timedelta(minutes=timeout_minutes).total_seconds()
    waited = 0.0
    interval = float(payload.get("poll_seconds", settings.heartbeat_seconds))
    while waited <= deadline:
        async with db() as session:
            bundle = await project_bundle(session, payload["project_id"])
            run = await session.get(Run, run_id)
            if run is not None and run.result is not None:
                return {"status": "finished", "result": run.result}
            ref = ExecRef(
                kind=(run.executor_kind if run and run.executor_kind else "fake"),
                name=(run.executor_ref if run and run.executor_ref else run_id),
                namespace=None,
                run_id=run_id,
            )
            status = await bundle.adapters.executor.status(ref)
        if status.finished:
            async with db() as session:
                bundle = await project_bundle(session, payload["project_id"])
                run = await session.get(Run, run_id)
                if run is not None and run.result is not None:
                    return {"status": "finished", "result": run.result}
                # Le runner n'a pas pu poster : on lit le résultat publié par l'exécuteur
                # (`result-url` côté Tekton), et on l'enregistre nous-mêmes.
                fetcher = getattr(bundle.adapters.executor, "fetch_result", None)
                fetched = await fetcher(ref) if fetcher is not None else None
                if fetched is not None:
                    payload_result = fetched.model_dump(mode="json", by_alias=True)
                    if run is not None:
                        run.result = payload_result
                    return {"status": "finished", "result": payload_result}
            if status.state == "succeeded":
                return {
                    "status": "missing_result",
                    "result": StageResult(
                        status=StageStatus.FAILED,
                        summary="le runner s'est terminé sans poster de résultat",
                        reason="invalid_result",
                    ).model_dump(mode="json", by_alias=True),
                }
            return {
                "status": status.state,
                "result": StageResult(
                    status=StageStatus.FAILED,
                    summary=status.message or f"exécution {status.state}",
                    reason=status.state,
                ).model_dump(mode="json", by_alias=True),
            }
        with contextlib.suppress(RuntimeError):  # hors contexte Temporal (tests unitaires)
            activity.heartbeat({"run_id": run_id, "waited_s": waited})
        await asyncio.sleep(interval)
        waited += interval
    return {
        "status": "timed_out",
        "result": StageResult(
            status=StageStatus.FAILED, summary=f"dépassement de {timeout_minutes} min", reason="timeout"
        ).model_dump(mode="json", by_alias=True),
    }


@activity.defn(name="cancel_run")
async def cancel_run(payload: dict[str, Any]) -> None:
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        run = await session.get(Run, payload["run_id"])
        if run is None:
            return
        ref = ExecRef(kind=run.executor_kind or "fake", name=run.executor_ref or run.id, run_id=run.id)
        await bundle.adapters.executor.cancel(ref)
        run.status = "cancelled"
        run.ended_at = utcnow()


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
                await session.execute(
                    __import__("sqlalchemy")
                    .select(GatewayKeyRow)
                    .where(GatewayKeyRow.key_id == run.gateway_key_id)
                )
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
        documents = dict(item.documents or {})
        for field in ("spec_markdown", "plan_markdown", "review_markdown", "release_notes_markdown"):
            value = getattr(outputs, field, None)
            if value:
                documents[field] = value
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
    from choregos_api.db.models import Finding as FindingRow
    from sqlalchemy import select

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
