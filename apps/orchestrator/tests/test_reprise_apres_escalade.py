"""Un ticket garé — escaladé, ou arrêté par une question — REPREND (#313).

Le contrat déclarait `needs_human.on_answer: resume` et rien ne le jouait : sur le locataire dev,
trois tickets escaladés n'avaient d'autre sortie que l'abandon, et « Replay the stage » (le geste
`rerun_stage`) était accepté par l'API puis ignoré par l'interpréteur.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_contracts import StageResult, StageStatus

from .conftest import Fixture, stage_result
from .test_interpreter import _runs, _ticket, _wait_state, _wait_until, scripted, start

pytestmark = pytest.mark.asyncio

ECHEC = StageResult(status=StageStatus.FAILED, summary="le clone a échoué", reason="agent_error")


async def _demandes(setup: Fixture) -> list[Any]:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        lignes = (
            await session.execute(select(HumanRequest).where(HumanRequest.work_item_id == setup.work_item_id))
        ).scalars()
        return list(lignes)


async def test_replay_the_stage_reprend_un_ticket_escalade(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Deux échecs épuisent `t-refine`, le ticket escalade ; « Replay the stage » le ramène à
    l'étape, avec un budget de tentatives NEUF — sans lui, le premier échec suivant réescaladerait."""
    scripted(setup.adapters, ECHEC, ECHEC, ECHEC, stage_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "l'abandon est proposé", lambda s: s["pending_request"])
        assert await _runs(setup) == 2

        await handle.signal("control", {"action": "rerun_stage", "by": "vincent"})
        # Un échec, puis la spec : la reprise a rendu ses deux tentatives à l'étape.
        await _wait_state(handle, "awaiting_spec_approval")
        status = await handle.query("status")
        assert await _runs(setup) == 4
        # Les numéros de tentative ne repartent pas de 1 : ils font l'identifiant du run.
        assert status["attempts"]["t-refine"] == 4, status["attempts"]
        await handle.signal("control", {"action": "stop"})
        await handle.result()

    abandons = [d for d in await _demandes(setup) if d.transition_id == "t-abandon"]
    assert abandons, "l'escalade proposait l'abandon"
    assert all(d.decided_at is not None for d in abandons), "l'abandon proposé est clos par la reprise"


async def test_une_reponse_reprend_l_etape_qui_a_pose_la_question(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`on_answer: resume` : la réponse ramène le ticket à l'étape, et l'agent qui reprend la LIT."""
    question = StageResult(
        status=StageStatus.NEEDS_HUMAN,
        summary="quelle devise ?",
        reason="question",
        questions=[{"text": "Which currency for credit notes?", "options": ["EUR", "order currency"]}],
    )
    scripted(setup.adapters, question, stage_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "l'abandon est proposé", lambda s: s["pending_request"])
        posee = next(d for d in await _demandes(setup) if d.kind == "question")

        await handle.signal(
            "human_decision",
            {
                "request_id": posee.id,
                "kind": "question",
                "approved": False,
                "answer": "the order currency",
                "decided_by": "vincent",
            },
        )
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal("control", {"action": "stop"})
        await handle.result()

    reprise = setup.adapters.executor.started[-1].stage_input
    assert reprise is not None and reprise.playbook.prompt is not None
    assert "Which currency for credit notes?" in reprise.playbook.prompt
    assert "the order currency" in reprise.playbook.prompt
    # La réponse a servi : l'étape suivante ne la relira pas.
    assert "human_answer" not in ((await _ticket(setup)).documents or {})
    # La question et l'abandon proposé sont clos. L'approbation de la nouvelle spec peut attendre —
    # ou pas encore exister : l'état se lit avant que sa demande soit créée (rouge en CI, #317).
    ouvertes = {d.transition_id for d in await _demandes(setup) if d.decided_at is None}
    assert ouvertes <= {"t-approve-spec"}, ouvertes


async def test_replay_hors_d_un_ticket_gare_est_oublie(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Sur un ticket qui attend une approbation ordinaire, `rerun_stage` ne déplace rien — et ne
    reste pas en réserve pour une escalade à venir."""
    scripted(setup.adapters, stage_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal("control", {"action": "rerun_stage"})
        await handle.signal("control", {"action": "pause"})
        await _wait_until(handle, "en pause", lambda s: s["paused"])
        status = await handle.query("status")
        assert status["state"] == "awaiting_spec_approval"
        assert await _runs(setup) == 1
        await handle.signal("control", {"action": "stop"})
        await handle.result()


async def test_une_relance_qui_ne_peut_pas_jouer_ne_boucle_pas(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un historique d'avant le marqueur — `patched` y a répondu non, et le SDK mémorise la
    réponse : `rerun_stage` ne peut pas jouer. Il doit être oublié. Sur le locataire dev (09/10),
    il restait en place, et le ticket recréait une demande d'abandon toutes les 4 s."""
    from choregos_orchestrator.workflows import interpreter

    vrai = interpreter.workflow.patched
    monkeypatch.setattr(
        interpreter.workflow,
        "patched",
        lambda marqueur: False if marqueur == interpreter.REPRISE_APRES_ESCALADE else vrai(marqueur),
    )
    scripted(setup.adapters, ECHEC, ECHEC)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "l'abandon est proposé", lambda s: s["pending_request"])
        await handle.signal("control", {"action": "rerun_stage"})
        import asyncio

        # Pas de pause ici : elle arrêtait la boucle au premier tour, et masquait le défaut. Une
        # attente qui boucle se voit à son historique, qui grossit à chaque demande recréée ; une
        # attente qui attend ne bouge pas.
        await asyncio.sleep(2)
        avant = len((await handle.fetch_history()).events)
        await asyncio.sleep(3)
        apres = len((await handle.fetch_history()).events)
        assert apres == avant, f"la relance a fait boucler l'attente humaine ({avant} → {apres} événements)"
        assert (await handle.query("status"))["state"] == "needs_human"
        await handle.signal("control", {"action": "stop"})
        await handle.result()
