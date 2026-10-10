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


# ───────────────────────── le ticket qui réveille le train (S22-22) ─────────────────────────


async def test_le_ticket_qui_reveille_le_train_reste_dans_le_lot(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """L'interpréteur embarque par signal-with-start : quand aucun train ne tourne, c'est SON signal
    qui le démarre. `run` écrasait alors le lot — #4 et #6 du locataire dev (09/10) ont réveillé leur
    train et en ont été effacés."""
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await temporal_env.client.start_workflow(
                "ReleaseTrain",
                {"project_slug": setup.project_slug, "env": "prod"},
                id=f"train-{setup.project_slug}-prod",
                task_queue="test",
                start_signal="merged",
                start_signal_args=[{"work_item_key": "varga/billing-api#4", "sha": "a4"}],
            )
            etat = await _jusqu_a(train, "status_query", lambda s: s["status"] == "collecting")
            assert etat["pending_items"] == ["varga/billing-api#4"], etat
            await train.signal("merged", {"work_item_key": "varga/billing-api#4", "sha": "a4"})
            etat = await _jusqu_a(train, "status_query", lambda s: s["status"] == "collecting")
            assert etat["batch_size"] == 1, "un second embarquement du même ticket ne le double pas"
            await train.signal("abort", {"by": "test"})
            await train.result()


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


# ───────────────────── la voie express : depuis le ticket, sous SON approbation (#279) ─────────────────────

HOTFIX_CAPTAINS = {"group": "hotfix-captains", "required": True}
#: Le cas dont l'historique s'archive (`CHOREGOS_ARCHIVER_HISTORIQUE=1`). `train-express-ancien-code`
#: a été enregistré sur `main` avant ce changement, cas `voie-dispense` : il rougit si la décision
#: sort du marqueur `express-lane-approval`.
ARCHIVES_EXPRESS = {"voie-exige": "train-express-S22-17"}


async def _voie_express(setup: Fixture, *, approbation: bool, voie: dict[str, Any] | None) -> None:
    """L'approbation ordinaire de la prod, et celle de sa voie express (`None` : la voie n'en dit rien)."""
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
        prod = document["release_train"]["prod"]
        prod["approval"]["required"] = approbation
        if voie is None:
            prod["express_lane"].pop("approval", None)
        else:
            prod["express_lane"]["approval"] = voie
        ligne.json_doc = document


def _departs_express(historique: Any) -> list[bool]:
    """Chaque départ du train, express ou non, tel que l'historique l'a planifié (`create_release`)."""
    departs: list[bool] = []
    for evenement in historique.events:
        attributs = evenement.activity_task_scheduled_event_attributes
        if evenement.HasField("activity_task_scheduled_event_attributes") and (
            attributs.activity_type.name == "create_release"
        ):
            departs.append(bool(json.loads(attributs.input.payloads[0].data).get("express")))
    return departs


async def _groupes_sollicites() -> list[str | None]:
    """Le groupe de chaque demande d'approbation de départ, dans l'ordre."""
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        lignes = await session.execute(
            select(Event).where(Event.type == "choregos.release.approval_requested").order_by(Event.ts)
        )
        return [(e.payload or {}).get("group") for e in lignes.scalars()]


async def test_un_ticket_etiquete_hotfix_embarque_par_l_interpreteur_prend_la_voie_express(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`signal_train` embarquait avec `labels: []` : un hotfix venu de l'interpréteur attendait le cron."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services import noter_les_etiquettes

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        noter_les_etiquettes(item, ["agent-ready", "hotfix"])
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket, embarquement = await _jusqu_au_train(setup, temporal_env)
            assert embarquement["labels"] == ["agent-ready", "hotfix"], "le ticket embarque ses étiquettes"

            train = await _train(temporal_env, setup)
            await train.signal("merged", embarquement)  # aucun `depart_now` : le hotfix part seul
            await _jusqu_a(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            await train.signal("abort", {"by": "test"})
            await train.result()
            await ticket.cancel()
            historique = await train.fetch_history()
    assert _departs_express(historique) == [True], "parti par la voie express, sans attendre le cron"


@pytest.mark.parametrize(
    ("approbation", "voie", "ticket", "attendu"),
    [
        # La politique ne demande rien, la voie express si : le hotfix attend SES capitaines.
        (False, HOTFIX_CAPTAINS, None, ["hotfix-captains"]),
        # La politique demande, la voie express non : le hotfix part sans attendre.
        (True, {"required": False}, None, []),
        # … sauf si un ticket du lot l'exige (ADR 0041) : l'exigence des tickets tient toujours.
        (True, {"required": False}, CAPITAINES, ["release-captains"]),
        # Une voie express qui ne dit rien de l'approbation garde celle de la politique.
        (True, None, None, ["release-captains"]),
    ],
    ids=["voie-exige", "voie-dispense", "ticket-exige", "voie-muette"],
)
async def test_un_depart_express_prend_l_approbation_de_la_voie_express(
    setup: Fixture,
    temporal_env: Any,
    worker_factory: Any,
    approbation: bool,
    voie: dict[str, Any] | None,
    ticket: dict[str, Any] | None,
    attendu: list[str],
    request: pytest.FixtureRequest,
) -> None:
    await _voie_express(setup, approbation=approbation, voie=voie)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    embarquement: dict[str, Any] = {"work_item_key": "varga/billing-api#9", "sha": "f1", "labels": ["hotfix"]}
    if ticket is not None:
        embarquement["approval"] = ticket
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _train(temporal_env, setup)
            await train.signal("merged", embarquement)
            etat = await _jusqu_a(
                train,
                "status_query",
                lambda s: (
                    s["status"] == "awaiting_approval" or (s["status"] == "collecting" and s["release_id"])
                ),
            )
            if etat["status"] == "awaiting_approval":
                await train.signal("approve", {"by": "marie"})
                await _jusqu_a(train, "status_query", lambda s: s["status"] == "collecting")
            await train.signal("abort", {"by": "test"})
            await train.result()
            historique = await train.fetch_history()
    # L'historique rejoue contre le code courant ; celui de la voie qui exige s'archive pour les suivants.
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(historique)
    nom = ARCHIVES_EXPRESS.get(request.node.callspec.id)
    if nom and os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        cible = HISTORIQUES / f"{nom}.json"
        cible.write_text(json.dumps(json.loads(historique.to_json()), indent=1) + "\n", encoding="utf-8")
    assert _departs_express(historique) == [True]
    (release,) = await _releases()
    assert release.status == "done"
    assert await _groupes_sollicites() == attendu


# ───────────────────────── un train qui livre sans Slack (S22-26) ─────────────────────────


async def test_le_train_livre_et_previent_le_ticket_sans_notificateur(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Sur le locataire dev, le 10/10, sans Slack : promotion et vérification faites, `finish_release`
    levait sur la notification, et les deux trains mouraient sans prévenir leurs tickets."""
    await _politique_de_prod(setup, approbation=False)
    setup.adapters.cd.set_health("billing-api", "Healthy")
    setup.adapters.notify.panne = "[slack] ni `bot_token` ni `webhook_url` configurés"
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket, embarquement = await _jusqu_au_train(setup, temporal_env)
            train = await _train(temporal_env, setup)
            await train.signal("merged", embarquement)
            await train.signal("depart_now", {"by": "augustin"})
            await _jusqu_a(train, "status_query", lambda s: s["status"] == "awaiting_approval")
            await train.signal("approve", {"by": "marie"})
            issue = await asyncio.wait_for(ticket.result(), timeout=60)
            await train.signal("abort", {"by": "test"})
            await train.result()
    assert issue["state"] == "deployed_prod"
    assert [release.status for release in await _releases()] == ["done"]
    assert setup.adapters.notify.sent == []
