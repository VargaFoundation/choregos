"""Un ticket garé — escaladé, ou arrêté par une question — REPREND (#313).

Le contrat déclarait `needs_human.on_answer: resume` et rien ne le jouait : sur le locataire dev,
trois tickets escaladés n'avaient d'autre sortie que l'abandon, et « Replay the stage » (le geste
`rerun_stage`) était accepté par l'API puis ignoré par l'interpréteur.
"""

from __future__ import annotations

import json
import os
import pathlib
from typing import Any

import pytest
from choregos_contracts import StageResult, StageStatus

from .conftest import Fixture, refine_result
from .test_interpreter import _runs, _ticket, _wait_state, _wait_until, scripted, start

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
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
    """Two failures exhaust `t-refine` and the work item escalates; "Replay the stage" brings it
    back for ONE more attempt (S27-13): it succeeds and the spec arrives."""
    scripted(setup.adapters, ECHEC, ECHEC, refine_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "abandon is offered", lambda s: s["pending_request"])
        assert await _runs(setup) == 2

        await handle.signal("control", {"action": "rerun_stage", "by": "vincent"})
        await _wait_state(handle, "awaiting_spec_approval")
        status = await handle.query("status")
        assert await _runs(setup) == 3
        # Les numéros de tentative ne repartent pas de 1 : ils font l'identifiant du run.
        assert status["attempts"]["t-refine"] == 3, status["attempts"]
        await handle.signal("control", {"action": "stop"})
        await handle.result()

    abandons = [d for d in await _demandes(setup) if d.transition_id == "t-abandon"]
    assert abandons, "l'escalade proposait l'abandon"
    assert all(d.decided_at is not None for d in abandons), "l'abandon proposé est clos par la reprise"


async def test_a_rerun_buys_one_attempt_not_a_new_allowance(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """S27-13: `max_attempts` is 2. Each rerun is ONE more attempt: a rerun whose attempt fails
    escalates again at once (before: it had two fresh attempts, and the third failure of the
    script would have been played), and every click pays for exactly one run."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    scripted(setup.adapters, ECHEC, ECHEC, ECHEC, ECHEC, ECHEC, refine_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "abandon is offered", lambda s: s["pending_request"])
        assert await _runs(setup) == 2
        for expected in (3, 4):
            await handle.signal("control", {"action": "rerun_stage", "by": "vincent"})
            await _wait_until(
                handle, "the rerun is played", lambda s, n=expected: s["attempts"]["t-refine"] == n
            )
            await _wait_state(handle, "needs_human")
            await _wait_until(handle, "the new abandon is proposed", lambda s: s["pending_request"])
            assert await _runs(setup) == expected, "a rerun plays one run, not a new allowance"
        status = await handle.query("status")
        assert status["state"] == "needs_human", status
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        target = HISTORIES / "wi-rerun-one-attempt-S27-13.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


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
    scripted(setup.adapters, question, refine_result("spec"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "abandon is offered", lambda s: s["pending_request"])
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
    scripted(setup.adapters, refine_result("spec"))
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
        await _wait_until(handle, "abandon is offered", lambda s: s["pending_request"])
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
