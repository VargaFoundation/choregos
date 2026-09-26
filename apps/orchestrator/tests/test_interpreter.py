"""`WorkflowInterpreter` : traversée complète, attente humaine, reprises, contrôle."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import StageResult, StageStatus

from .conftest import Fixture, stage_result

pytestmark = pytest.mark.asyncio


def scripted(adapters: Any, *results: StageResult) -> None:
    for result in results:
        adapters.executor.queue_result(result)


async def start(env: Any, setup: Fixture, task_queue: str = "test") -> Any:
    return await env.client.start_workflow(
        "WorkflowInterpreter",
        {
            "project_id": setup.project_id,
            "project_slug": setup.project_slug,
            "work_item_id": setup.work_item_id,
            "tracker_key": setup.tracker_key,
        },
        id=f"wi-{setup.project_slug}-123",
        task_queue=task_queue,
    )


async def test_default_simple_full_traversal(setup: Fixture, temporal_env: Any, worker_factory: Any) -> None:
    """Un ticket traverse `default-simple` de bout en bout : agents, humain, système, train."""
    scripted(
        setup.adapters,
        stage_result("spec rédigée", outputs={"size": "M", "risk": "low", "spec_markdown": "## Spec"}),
        stage_result("implémenté", artifacts={"branch": "choregos/123", "commits": ["fix(orders): avoirs"]}),
        stage_result("vérifié"),
    )
    setup.adapters.cd.set_health("billing-api", "Healthy")

    async with worker_factory():
        handle = await start(temporal_env, setup)

        # 1. l'agent `refine` a tourné, la validation humaine est demandée
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal(
            "human_decision", {"approved": True, "decided_by": "augustin", "kind": "approval"}
        )

        # 2. implémentation, vérification, PR : la CI et la review arrivent par événements
        await _wait_state(handle, "pr_open")
        await handle.signal(
            "inbound",
            {
                "type": "scm.check.completed",
                "source": "github",
                "delivery_id": "d1",
                "payload": {"conclusion": "success"},
            },
        )
        setup.adapters.scm.set_checks(_pr_ref(setup), "success")
        setup.adapters.scm.submit_review(_pr_ref(setup), "marie", "approved")
        await handle.signal(
            "inbound",
            {
                "type": "scm.pr.review_submitted",
                "source": "github",
                "delivery_id": "d2",
                "payload": {"state": "approved"},
            },
        )

        # 3. le train annonce le déploiement réussi
        await _wait_state(handle, "merged")
        await handle.signal(
            "inbound",
            {
                "type": "cd.rollout.completed",
                "source": "argocd",
                "delivery_id": "d3",
                "payload": {"env": "prod"},
            },
        )
        outcome = await handle.result()

    assert outcome["state"] == "deployed_prod"
    assert outcome["cost_usd"] >= 0
    comment = setup.adapters.tracker.status_comment(setup.tracker_key)
    assert comment is not None and "Choregos — suivi" in comment
    assert "agent refine" in comment and "agent implement" in comment


async def test_human_rejection_returns_to_refining(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    scripted(setup.adapters, stage_result("spec v1"), stage_result("spec v2"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal(
            "human_decision",
            {
                "approved": False,
                "decided_by": "augustin",
                "kind": "approval",
                "reason": "périmètre trop large",
            },
        )
        await _wait_state(handle, "awaiting_spec_approval")
        status = await handle.query("status")
        assert status["state"] == "awaiting_spec_approval"
        await handle.signal("control", {"action": "stop"})
        await handle.result()


async def test_failures_are_bounded_then_escalate(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Deux tentatives, puis escalade vers `needs_human` : jamais de boucle infinie."""
    failure = StageResult(status=StageStatus.FAILED, summary="l'agent a planté", reason="agent_error")
    scripted(setup.adapters, failure, failure, failure)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        status = await handle.query("status")
        assert status["attempts"]["t-refine"] == 2, status["attempts"]
        await handle.signal("control", {"action": "stop"})
        await handle.result()


async def test_agent_question_creates_human_request(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    scripted(
        setup.adapters,
        StageResult(
            status=StageStatus.NEEDS_HUMAN,
            summary="quelle devise pour les avoirs ?",
            reason="question",
            questions=[{"text": "Quelle devise ?", "options": ["EUR", "devise de la commande"]}],
        ),
    )
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await handle.signal("control", {"action": "stop"})
        await handle.result()

    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (await session.execute(select(HumanRequest))).scalars().all()
    assert any(row.kind == "question" for row in rows)
    comments = setup.adapters.tracker.items[setup.tracker_key].comments
    assert any("Choregos — question" in c.body for c in comments)


async def test_pause_and_resume(setup: Fixture, temporal_env: Any, worker_factory: Any) -> None:
    scripted(setup.adapters, stage_result("spec"), stage_result("implémenté"), stage_result("vérifié"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal("control", {"action": "pause"})
        await handle.signal("human_decision", {"approved": True, "decided_by": "a", "kind": "approval"})
        status = await handle.query("status")
        assert status["paused"] is True
        await handle.signal("control", {"action": "resume"})
        # Ce qu'on veut prouver, c'est que la reprise DÉBLOQUE : plus en pause, et le workflow
        # a quitté l'attente humaine. Attendre `in_progress` par égalité attendait un état de
        # passage, que le scrutin rate dès que la machine va vite.
        await _wait_until(
            handle,
            "le workflow a repris",
            lambda s: not s["paused"] and s["state"] != "awaiting_spec_approval",
            timeout=40,
        )
        await handle.signal("control", {"action": "stop"})
        await handle.result()


async def test_cost_is_counted_once_per_run(setup: Fixture, temporal_env: Any, worker_factory: Any) -> None:
    """Le coût vient du gateway, une seule fois par run — pas de double comptage."""
    scripted(setup.adapters, stage_result("spec"))
    gateway = setup.adapters.gateway
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        key_id = next(iter(gateway.keys), None) or next(iter(gateway.spends))
        gateway.spends[key_id] = _spend()
        await handle.signal("control", {"action": "stop"})
        await handle.result()

    from choregos_api.db.models import CostLedger, Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        runs = (await session.execute(select(Run))).scalars().all()
        ledger = (await session.execute(select(CostLedger))).scalars().all()
    assert len(runs) == 1
    assert len(ledger) == 1, "une seule ligne de coût par run"


def _spend() -> Any:
    from choregos_core import Spend

    return Spend(tokens_in=41000, tokens_out=3000, tokens_cached=28000, cost_usd=0.34, requests=12)


def _pr_ref(setup: Fixture) -> Any:
    from choregos_core.domain import PrRef

    prs = list(setup.adapters.scm.prs.values())
    if prs:
        return prs[0].ref
    return PrRef(repo="varga/billing-api", number=1)


async def _wait_until(handle: Any, description: str, predicat: Any, *, timeout: int = 60) -> None:
    """Attend qu'une CONDITION soit vraie sur le `status` du workflow.

    Attendre un état par égalité ne marche que pour un état **stable** — un état où le workflow
    s'arrête (une attente humaine, une fin). Pour un état de passage, le scrutin à 100 ms le rate
    une fois sur deux : `test_pause_and_resume` attendait `in_progress` après une reprise et a
    rougi en CI sur `pr_open`, parce que les étapes suivantes étaient déjà passées. Un test qui
    échoue selon la charge de la machine ne dit rien sur le code.
    """
    import asyncio

    for _ in range(timeout * 10):
        status = await handle.query("status")
        if predicat(status):
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"{description} : jamais vrai (status : {await handle.query('status')})")


async def _wait_state(handle: Any, state: str, *, timeout: int = 60) -> None:
    """Attend un état **stable**, via la requête `status`.

    Le paramètre `after=` a existé ici et n'était **jamais lu** : `test_refus_de_spec` croyait
    vérifier un passage par `refining` et ne vérifiait rien. Il n'est pas remplacé — reconstruire
    un historique à coups de scrutin serait précisément la même illusion.
    """
    await _wait_until(handle, f"état `{state}`", lambda s: s["state"] == state, timeout=timeout)


# ───────────────────────── un ticket mort se voit (2026-09-24) ─────────────────────────


def _worker_avec(temporal_env: Any, remplacements: dict[str, Any]) -> Any:
    """Le worker complet, où quelques activités sont remplacées par leur doublure."""
    from choregos_orchestrator.activities import ALL_ACTIVITIES
    from choregos_orchestrator.workflows import ALL_WORKFLOWS, WORKFLOW_ACTIVITIES
    from temporalio.worker import Worker

    gardees = [a for a in [*ALL_ACTIVITIES, *WORKFLOW_ACTIVITIES] if a.__name__ not in remplacements]
    return Worker(
        temporal_env.client,
        task_queue="test",
        workflows=ALL_WORKFLOWS,
        activities=[*gardees, *remplacements.values()],
    )


async def _ticket(setup: Fixture) -> Any:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        session.expunge(item)
        return item


async def test_un_interpreteur_qui_meurt_le_dit(setup: Fixture, temporal_env: Any) -> None:
    """Une activité en échec définitif tue le workflow — et le ticket porte la cause.

    Banc du 2026-09-24 : deux tickets RH morts, l'un sur une activité, l'autre sur un
    heartbeat, et rien en base ne le disait. Le workflow écrit désormais pourquoi il meurt.
    """
    from temporalio import activity
    from temporalio.client import WorkflowFailureError
    from temporalio.exceptions import ApplicationError

    @activity.defn(name="await_run")
    async def await_run_qui_meurt(payload: dict[str, Any]) -> dict[str, Any]:
        raise ApplicationError("le projet n'a plus de dépôt", non_retryable=True)

    async with _worker_avec(temporal_env, {"await_run": await_run_qui_meurt}):
        handle = await start(temporal_env, setup)
        with pytest.raises(WorkflowFailureError):
            await handle.result()

    item = await _ticket(setup)
    assert item.failure is not None, "le ticket ne dit pas que son interpréteur est mort"
    assert item.failure["message"] == "le projet n'a plus de dépôt"
    assert item.failure["activity"] == "await_run"
    assert item.failure["state"] == "inbox"  # l'état d'où l'étape partait

    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        types = (
            (await session.execute(select(Event.type).where(Event.work_item_id == setup.work_item_id)))
            .scalars()
            .all()
        )
    assert "choregos.workitem.workflow_failed" in types


async def test_un_interpreteur_qui_redemarre_efface_la_marque(setup: Fixture, temporal_env: Any) -> None:
    """La marque tombe au redémarrage : un ticket relancé n'est plus « mort »."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from temporalio import activity
    from temporalio.exceptions import ApplicationError

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.failure = {"message": "mort une première fois", "at": "2026-09-24T15:04:00+00:00"}

    @activity.defn(name="await_run")
    async def await_run_qui_bloque(payload: dict[str, Any]) -> dict[str, Any]:
        raise ApplicationError("on s'arrête ici", non_retryable=True)

    async with _worker_avec(temporal_env, {"await_run": await_run_qui_bloque}):
        handle = await start(temporal_env, setup)
        with pytest.raises(Exception):  # noqa: B017 — la mort de ce workflow est voulue
            await handle.result()
    # `load_context` a effacé l'ancienne marque au démarrage ; la nouvelle mort a posé la sienne
    item = await _ticket(setup)
    assert item.failure is not None
    assert item.failure["message"] == "on s'arrête ici"


async def test_le_diff_non_collecte_ne_tue_pas_l_etape(setup: Fixture, temporal_env: Any) -> None:
    """`collect_run_artifacts` sert l'affichage : son échec ne coûte pas le ticket (RH-1, 24/09)."""
    from temporalio import activity
    from temporalio.exceptions import ApplicationError

    scripted(
        setup.adapters,
        stage_result("spec rédigée", outputs={"size": "M", "risk": "low", "spec_markdown": "## Spec"}),
    )

    @activity.defn(name="collect_run_artifacts")
    async def collect_qui_echoue(payload: dict[str, Any]) -> dict[str, Any]:
        raise ApplicationError("le projet n'a pas de dépôt", non_retryable=True)

    async with _worker_avec(temporal_env, {"collect_run_artifacts": collect_qui_echoue}):
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")

    assert (await _ticket(setup)).failure is None


async def test_await_run_est_rejoue_apres_une_panne_passagere(setup: Fixture, temporal_env: Any) -> None:
    """Un worker qui redémarre pendant un run (heartbeat manqué) ne tue plus le ticket."""
    from choregos_orchestrator.activities import stage as stage_activities
    from temporalio import activity
    from temporalio.exceptions import ApplicationError

    scripted(
        setup.adapters,
        stage_result("spec rédigée", outputs={"size": "M", "risk": "low", "spec_markdown": "## Spec"}),
    )
    appels: list[int] = []

    @activity.defn(name="await_run")
    async def await_run_fragile(payload: dict[str, Any]) -> dict[str, Any]:
        appels.append(1)
        if len(appels) == 1:
            raise ApplicationError("heartbeat manqué : le worker a redémarré")  # rejouable
        return await stage_activities.await_run(payload)

    async with _worker_avec(temporal_env, {"await_run": await_run_fragile}):
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")

    assert len(appels) >= 2
    assert (await _ticket(setup)).failure is None


async def test_les_verdicts_des_garanties_sont_dans_le_journal_du_run(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Quelles garanties ont tourné, et ce qu'elles ont répondu : c'est dans le journal du run."""
    from choregos_api.db.models import Run, RunEvent
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    scripted(
        setup.adapters,
        stage_result("spec rédigée", outputs={"size": "M", "risk": "low", "spec_markdown": "## Spec"}),
        stage_result("implémenté", artifacts={"branch": "choregos/123", "commits": ["fix(orders): avoirs"]}),
        stage_result("vérifié"),
    )
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal(
            "human_decision", {"approved": True, "decided_by": "augustin", "kind": "approval"}
        )
        # l'implémentation porte les garanties de périmètre et de preuves
        await _wait_state(handle, "pr_open")

    async with session_scope(orgs="*") as session:
        runs = (
            (await session.execute(select(Run).where(Run.work_item_id == setup.work_item_id))).scalars().all()
        )
        rows = (
            (
                await session.execute(
                    select(RunEvent)
                    .where(RunEvent.run_id.in_([r.id for r in runs]), RunEvent.type == "gate.outcome")
                    .order_by(RunEvent.seq)
                )
            )
            .scalars()
            .all()
        )
    assert rows, "aucun verdict journalisé"
    noms = {r.payload["name"] for r in rows}
    assert "scope_respected" in noms or "evidence_present" in noms, noms
    assert all(isinstance(r.payload["passed"], bool) and "detail" in r.payload for r in rows)
