"""La préparation d'une étape agent : modèle, clé, contexte, `StageInput`.

Chaque fonction est une activité Temporal ; toutes sont idempotentes par `run_id`. Le
lancement et l'attente vivent dans `execution`, le bilan dans `bilan`, la garde contre
l'injection dans `garde` — ce module les réexporte, l'interpréteur et les tests continuent
de les lire ici.
"""

from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import Any

from choregos_api.db.models import GatewayKeyRow, Run
from choregos_api.logging import bind, get_logger
from choregos_api.security import mint_run_token
from choregos_api.services import persist_event
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
    StageRole,
    ToolsRef,
    TransitionRef,
    WorkItemLinks,
    WorkItemRef,
)
from choregos_core import KNOWN_BACKEND_NAMES, ModelResolutionError, ModelResolver, utcnow
from sqlalchemy import select
from temporalio import activity

from ..config import get_settings
from .base import db, load_work_item, project_bundle
from .bilan import collect_spend, record_run_outcome
from .execution import (
    FILE_MAX_MINUTES,
    await_run,
    cancel_run,
    classe_d_execution,
    env_du_runner,
    start_run,
)
from .garde import garde_contre_l_injection
from .plan import StagePlan

__all__ = [
    "FILE_MAX_MINUTES",
    "StagePlan",
    "await_run",
    "cancel_run",
    "classe_d_execution",
    "collect_spend",
    "env_du_runner",
    "prepare_stage",
    "record_run_outcome",
    "start_run",
]

logger = get_logger("choregos.stage")


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
        bind(project=bundle.slug, work_item=item.tracker_key, stage=plan.role)
        run_id = _run_id(plan)

        existing = await session.get(Run, run_id)
        if existing is not None and existing.stage_input:
            return {"run_id": run_id, "stage_input": existing.stage_input, "reused": True}

        engine = bundle.engine
        resolver = ModelResolver(gateway_url=settings.gateway_url)
        backend = plan.backend or bundle.config.agent.default_backend
        if plan.role == str(StageRole.REVIEW) and engine.cross_backend_review():
            backend = await _reviewer_backend(session, bundle, item, backend, resolver, plan)
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
        # Rejouable : une activité Temporal peut repasser ici sur le MÊME run, et la clé
        # est déterministe. Un `INSERT` sec répondait alors « duplicate key value violates
        # unique constraint "ix_gateway_keys_key_id" » — une erreur de base remontée telle
        # quelle jusqu'au workflow, qui mourait. Vu sur le banc du 2026-09-24.
        # AGENTS.md l'exige : toute activité est rejouable sans effet double.
        existante = (
            await session.execute(select(GatewayKeyRow).where(GatewayKeyRow.key_id == key.key_id))
        ).scalar_one_or_none()
        if existante is None:
            session.add(
                GatewayKeyRow(
                    key_id=key.key_id,
                    run_id=run_id,
                    project_id=bundle.project.id,
                    budget_usd=budget.usd,
                    expires_at=key.expires_at,
                )
            )
        else:
            existante.run_id = run_id
            existante.budget_usd = budget.usd
            existante.expires_at = key.expires_at

        context_pack = await _context_pack(bundle, item, plan)
        await garde_contre_l_injection(session, bundle, item, run_id, plan, context_pack)
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
        depot = bundle.config.repo
        stage_input = StageInput(
            run_id=run_id,
            attempt=plan.attempt,
            project=ProjectRef(
                slug=bundle.slug,
                org=bundle.org_slug,
                test_command=depot.test_command if depot else "",
                lint_command=depot.lint_command if depot else "",
                typecheck_command=depot.typecheck_command if depot else "",
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
            # Pas de dépôt, pas de `repo` : le runner prépare un répertoire vide au lieu de
            # cloner. Un dépôt factice, comme on le faisait, faisait croire à un workspace
            # qui n'existait pas et promettait une branche que personne ne relirait.
            repo=RepoRef(
                url=depot.url,
                base_branch=depot.default_branch,
                work_branch=bundle.config.branch_for(item.tracker_key),
                clone_depth=depot.clone_depth,
            )
            if depot
            else None,
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
                unknown_requests=bundle.policy.sandbox.unknown_requests,
            ),
            callbacks=Callbacks(api_url=f"{settings.callback_url}/api/v1/internal", run_token=token),
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

    documents = item.documents or {}
    return render_playbook(
        plan.playbook or plan.role,
        ticket={"key": item.tracker_key, "title": item.title, "body": item.body_snapshot or ""},
        spec=documents.get("spec_markdown", ""),
        plan_markdown=documents.get("plan_markdown", ""),
        # Les entrées que la transition déclare (`inputs: [profils]`), lues sous leur nom :
        # c'est ainsi qu'une étape métier reçoit ce que la précédente a produit.
        inputs={nom: documents.get(nom, "") for nom in (plan.inputs or [])},
        allowed_paths=list(item.allowed_paths or []),
        context=context,
        project=bundle.config,
    )


async def _reviewer_backend(
    session: Any,
    bundle: Any,
    item: Any,
    wanted: str,
    resolver: ModelResolver,
    plan: StagePlan,
) -> str:
    """Un relecteur qui n'est pas l'implémenteur (politique `review.cross_backend`).

    La garantie est un mécanisme : si le backend prévu pour la revue est celui qui a écrit
    le code, on en prend un autre — parmi ceux que le projet autorise et qui acceptent le
    modèle demandé. Si aucun ne convient, on garde celui d'origine et on le dit : bloquer
    un ticket parce qu'un seul backend est installé serait pire que la revue dégradée, et
    `GET /projects/{id}/metrics/cross-backend` compte ces cas.
    """
    implementer = (
        await session.execute(
            select(Run.backend)
            .where(
                Run.work_item_id == item.id,
                Run.stage_role == str(StageRole.IMPLEMENT),
                Run.backend.is_not(None),
            )
            .order_by(Run.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not implementer or implementer != wanted:
        return wanted
    allowed = list(bundle.config.agent.allowed_backends) or list(KNOWN_BACKEND_NAMES)
    for candidate in allowed:
        if candidate == implementer:
            continue
        try:
            resolver.resolve(plan.model_request, project=bundle.config, size=item.size, backend=candidate)
        except ModelResolutionError:
            continue
        logger.info(
            "revue croisée",
            project=bundle.slug,
            work_item=item.tracker_key,
            implementer=implementer,
            reviewer=candidate,
        )
        return str(candidate)
    logger.warning(
        "revue croisée impossible",
        project=bundle.slug,
        work_item=item.tracker_key,
        implementer=implementer,
        raison="aucun autre backend n'accepte le modèle demandé",
    )
    return wanted
