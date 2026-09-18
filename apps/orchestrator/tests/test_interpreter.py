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
        await _wait_state(handle, "awaiting_spec_approval", after="refining")
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
        await _wait_state(handle, "in_progress", timeout=40)
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


async def _wait_state(handle: Any, state: str, *, after: str | None = None, timeout: int = 60) -> None:
    """Attend qu'un workflow atteigne un état, via sa requête `status`."""
    import asyncio

    for _ in range(timeout * 10):
        status = await handle.query("status")
        if status["state"] == state:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(
        f"état `{state}` jamais atteint (courant : {(await handle.query('status'))['state']})"
    )
