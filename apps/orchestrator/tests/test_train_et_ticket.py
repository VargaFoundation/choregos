"""Le train et le ticket se parlent (ADR 0041, S21-21).

La revue du 2026-10-07 l'a mesuré : le train ne disait jamais au ticket qu'il avait livré — le
ticket attendait un `cd.*` que rien n'envoyait, et les tests le lui portaient à la main —, et
l'approbation qu'un workflow exige du départ (`train.approval`) n'arrivait jamais jusqu'au train.
Ici, un VRAI `ReleaseTrain` fait partir le lot ; personne n'écrit d'événement de CD.
"""

from __future__ import annotations

import asyncio
import copy
import json
import os
import pathlib
from typing import Any

import pytest

from .conftest import Fixture, stage_result

pytestmark = pytest.mark.asyncio

CAPITAINES = {"required": True, "group": "release-captains"}
HISTORIQUES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


async def _politique_de_prod(setup: Fixture, *, approbation: bool) -> None:
    """La politique du projet (preset `solo`) exige l'approbation en prod ; ces tests la retirent
    pour prouver que l'exigence du WORKFLOW suffit à retenir le lot."""
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        ligne = (
            await session.execute(
                select(PolicyDef).where(
                    PolicyDef.project_id == setup.project_id, PolicyDef.is_active.is_(True)
                )
            )
        ).scalar_one()
        document = copy.deepcopy(ligne.json_doc)
        document["release_train"]["prod"]["approval"]["required"] = approbation
        ligne.json_doc = document


async def _train(env: Any, setup: Fixture) -> Any:
    return await env.client.start_workflow(
        "ReleaseTrain",
        {"project_slug": setup.project_slug, "env": "prod"},
        id=f"train-{setup.project_slug}-prod",
        task_queue="test",
    )


async def _jusqu_a(handle: Any, requete: str, predicat: Any, timeout: float = 60.0) -> dict[str, Any]:
    for _ in range(int(timeout * 10)):
        etat = await handle.query(requete)
        if predicat(etat):
            return dict(etat)
        await asyncio.sleep(0.1)
    raise AssertionError(f"jamais atteint (dernier : {await handle.query(requete)})")


async def _releases() -> list[Any]:
    from choregos_api.db.models import Release
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return list((await session.execute(select(Release))).scalars())


# ───────────────────────── le train : l'approbation suit ce que le lot emporte ─────────────────────────


async def test_un_lot_sans_exigence_part_sans_approbation_quand_la_politique_n_en_demande_pas(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    await _politique_de_prod(setup, approbation=False)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _train(temporal_env, setup)
            await train.signal("merged", {"work_item_key": "varga/billing-api#1", "sha": "a1"})
            await train.signal("depart_now", {"by": "augustin"})
            await _jusqu_a(train, "status_query", lambda s: s["status"] == "collecting" and s["release_id"])
            await train.signal("abort", {"by": "test"})
            await train.result()
    (release,) = await _releases()
    assert release.status == "done" and release.approved_by is None


async def test_un_ticket_qui_exige_l_approbation_retient_tout_le_lot(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    await _politique_de_prod(setup, approbation=False)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _train(temporal_env, setup)
            await train.signal("merged", {"work_item_key": "varga/billing-api#1", "sha": "a1"})
            await train.signal(
                "merged", {"work_item_key": "varga/billing-api#2", "sha": "a2", "approval": CAPITAINES}
            )
            await train.signal("depart_now", {"by": "augustin"})
            await _jusqu_a(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            assert not setup.adapters.cd.rollouts, "le lot est parti avant l'approbation que son ticket exige"
            await train.signal("approve", {"by": "marie"})
            await _jusqu_a(
                train, "status_query", lambda s: s["status"] == "collecting" and s["batch_size"] == 0
            )
            await train.signal("abort", {"by": "test"})
            await train.result()
    (release,) = await _releases()
    assert release.status == "done" and release.approved_by == "marie"
    assert len(release.items) == 2, "le correctif part avec la fonctionnalité, sous la même approbation"


async def test_l_exigence_apportee_par_un_second_embarquement_s_ajoute(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Le webhook embarque d'abord le ticket sans exigence, l'interpréteur ensuite avec : le doublon
    était ignoré en entier, et l'approbation avec lui."""
    await _politique_de_prod(setup, approbation=False)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _train(temporal_env, setup)
            await train.signal("merged", {"work_item_key": "varga/billing-api#1", "sha": "a1"})
            await train.signal(
                "merged", {"work_item_key": "varga/billing-api#1", "sha": "a1", "approval": CAPITAINES}
            )
            await _jusqu_a(train, "status_query", lambda s: s["batch_size"] == 1)
            await train.signal("depart_now", {"by": "augustin"})
            await _jusqu_a(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            await train.signal("abort", {"by": "test"})
            await train.result()


# ───────────────────────── le ticket : il avance quand SON train a livré ─────────────────────────


async def _jusqu_au_train(setup: Fixture, temporal_env: Any) -> tuple[Any, dict[str, Any]]:
    """Un ticket `default-simple` mené jusqu'à la fusion ; rend son interpréteur et son embarquement."""
    from choregos_api.temporal import interpreter_id
    from choregos_orchestrator.train_client import fake_signals

    for resultat in (
        stage_result("spec", outputs={"size": "M", "risk": "low", "spec_markdown": "## Spec"}),
        stage_result("implémenté", artifacts={"branch": "choregos/123", "commits": ["fix(orders): avoirs"]}),
        stage_result("vérifié"),
    ):
        setup.adapters.executor.queue_result(resultat)
    ticket = await temporal_env.client.start_workflow(
        "WorkflowInterpreter",
        {
            "project_id": setup.project_id,
            "project_slug": setup.project_slug,
            "work_item_id": setup.work_item_id,
            "tracker_key": setup.tracker_key,
        },
        id=interpreter_id(setup.project_slug, setup.tracker_key),
        task_queue="test",
    )
    await _jusqu_a(ticket, "status", lambda s: s["state"] == "awaiting_spec_approval")
    await ticket.signal("human_decision", {"approved": True, "decided_by": "augustin", "kind": "approval"})
    await _jusqu_a(ticket, "status", lambda s: s["state"] == "pr_open")
    pr = next(iter(setup.adapters.scm.prs.values())).ref
    setup.adapters.scm.set_checks(pr, "success")
    setup.adapters.scm.submit_review(pr, "marie", "approved")
    for numero, (type_, charge) in enumerate(
        (
            ("scm.check.completed", {"conclusion": "success"}),
            ("scm.pr.review_submitted", {"state": "approved"}),
        )
    ):
        await ticket.signal(
            "inbound", {"type": type_, "source": "github", "delivery_id": f"d{numero}", "payload": charge}
        )
    train_id = f"train-{setup.project_slug}-prod"
    for _ in range(600):
        if fake_signals(train_id):
            break
        await asyncio.sleep(0.1)
    ((nom, embarquement),) = fake_signals(train_id)
    assert nom == "merged"
    return ticket, {k: v for k, v in embarquement.items() if k != "start_payload"}


async def test_le_ticket_avance_quand_son_train_a_livre_sans_evenement_porte_a_la_main(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    await _politique_de_prod(setup, approbation=False)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket, embarquement = await _jusqu_au_train(setup, temporal_env)
            assert embarquement["approval"] == CAPITAINES, "le capitaine que `default-simple` nomme"

            train = await _train(temporal_env, setup)
            await train.signal("merged", embarquement)
            await train.signal("depart_now", {"by": "augustin"})
            await _jusqu_a(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            await train.signal("approve", {"by": "marie"})
            issue = await asyncio.wait_for(ticket.result(), timeout=60)
            await train.signal("abort", {"by": "test"})
            await train.result()
            historiques = {"wi-train-S21-21": await ticket.fetch_history(),
                           "train-approbation-S21-21": await train.fetch_history()}  # fmt: skip
    assert issue["state"] == "deployed_prod"

    # Les deux historiques rejouent contre le code courant — et s'archivent pour les suivants.
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    for nom, historique in historiques.items():
        await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(historique)
        if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
            cible = HISTORIQUES / f"{nom}.json"
            cible.write_text(json.dumps(json.loads(historique.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_un_evenement_d_un_autre_environnement_ne_vaut_pas_livraison(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket, _ = await _jusqu_au_train(setup, temporal_env)
            await ticket.signal(
                "inbound",
                {"type": "cd.rollout.completed", "source": "release-train", "delivery_id": "staging-1",
                 "payload": {"env": "staging", "release_id": "r-staging"}},
            )  # fmt: skip
            await asyncio.sleep(1.5)
            assert (await ticket.query("status"))["state"] == "merged", "le staging terminé n'est pas la prod"
            await ticket.signal(
                "inbound",
                {"type": "cd.rollout.completed", "source": "release-train", "delivery_id": "prod-1",
                 "payload": {"env": "prod", "release_id": "r-prod"}},
            )  # fmt: skip
            issue = await asyncio.wait_for(ticket.result(), timeout=60)
    assert issue["state"] == "deployed_prod"


async def test_un_rollback_rend_le_ticket_a_un_humain_sans_attendre_le_delai(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    setup.adapters.cd.fail_next_analysis()
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket, embarquement = await _jusqu_au_train(setup, temporal_env)
            train = await _train(temporal_env, setup)
            await train.signal("merged", embarquement)
            await train.signal("depart_now", {"by": "augustin"})
            await _jusqu_a(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            await train.signal("approve", {"by": "marie"})
            await _jusqu_a(train, "status_query", lambda s: s["frozen"] is True)
            await _jusqu_a(ticket, "status", lambda s: s["state"] == "needs_human")
            await train.signal("abort", {"by": "test"})
            await train.result()
            await ticket.cancel()
