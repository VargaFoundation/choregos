"""Rattrapage du tracker : ce qu'un webhook perdu laisse derrière lui est repris (S3-06)."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_orchestrator.activities import tracker as tracker_activities
from choregos_orchestrator.train_client import clear_fake_signals, fake_starts

from .conftest import Fixture

pytestmark = pytest.mark.asyncio


def _seed_candidate(setup: Fixture, title: str) -> str:
    """Un ticket étiqueté `agent-ready` côté tracker, qu'aucun webhook n'a annoncé."""
    return setup.adapters.tracker.seed(title, labels=["agent-ready"])


async def test_un_ticket_jamais_annonce_est_cree_et_demarre(setup: Fixture) -> None:
    clear_fake_signals()
    key = _seed_candidate(setup, "Le taux de TVA réduit n'est pas appliqué")

    result = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})

    assert key in result["created"], result
    assert key in result["started"], result
    assert any(key.replace("/", "_").replace("#", "-") in wf for wf in fake_starts()), fake_starts()

    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (await session.execute(select(WorkItem).where(WorkItem.tracker_key == key))).scalar_one()
        assert row.title == "Le taux de TVA réduit n'est pas appliqué"
        assert row.temporal_wf_id, "le ticket rattrapé porte l'identifiant de son workflow"


async def test_le_rattrapage_est_idempotent(setup: Fixture) -> None:
    """Deux passages ne créent pas deux tickets, et ne relancent pas le workflow."""
    clear_fake_signals()
    _seed_candidate(setup, "Doublon éventuel")

    first = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})
    second = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})

    assert first["created"] and first["started"]
    assert second["created"] == [] and second["started"] == []
    assert len(fake_starts()) == 1


async def test_un_ticket_deja_connu_n_est_pas_redemarre(setup: Fixture) -> None:
    """Le ticket du décor existe déjà : le rattrapage ne doit pas doubler son workflow."""
    clear_fake_signals()
    setup.adapters.tracker.items[setup.tracker_key].labels = ["agent-ready"]

    result = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})

    assert result["created"] == []
    assert result["started"] == [setup.tracker_key], "il n'avait pas encore de workflow"

    encore = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})
    assert encore["started"] == []


async def test_un_ticket_termine_ou_en_pause_est_laisse_tranquille(setup: Fixture) -> None:
    clear_fake_signals()
    setup.adapters.tracker.items[setup.tracker_key].labels = ["agent-ready"]
    termine = _seed_candidate(setup, "Déjà livré")

    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(select(WorkItem).where(WorkItem.tracker_key == setup.tracker_key))
        ).scalar_one()
        row.paused = True

    result = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})
    assert setup.tracker_key not in result["started"], "un ticket en pause reste en pause"
    assert termine in result["started"]

    async with session_scope() as session:
        row = (await session.execute(select(WorkItem).where(WorkItem.tracker_key == termine))).scalar_one()
        row.state = "deployed_prod"
    clear_fake_signals()
    encore = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})
    assert encore["started"] == [], "un état terminal ne se relance pas"


async def test_sans_candidat_le_rattrapage_ne_fait_rien(setup: Fixture) -> None:
    clear_fake_signals()
    result = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})
    assert result == {"candidates": 0, "created": [], "started": []}
    assert fake_starts() == {}


async def test_la_boucle_periodique_rattrape_puis_se_termine(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Le workflow enchaîne les passages et s'arrête proprement sur `stop`."""
    clear_fake_signals()
    key = _seed_candidate(setup, "Rattrapé par la boucle")
    async with worker_factory("test"):
        handle = await temporal_env.client.start_workflow(
            "TrackerReconciliation",
            {"project_slug": setup.project_slug, "interval_seconds": 60, "max_passes": 3},
            id=f"reconcile-{setup.project_slug}",
            task_queue="test",
        )
        for _ in range(100):
            status = await handle.query("status")
            if status["passes"] >= 1:
                break
        await handle.signal("stop", {})
        outcome = await handle.result()

    assert outcome["stopped"] is True
    assert outcome["passes"] >= 1
    assert any(key.replace("/", "_").replace("#", "-") in wf for wf in fake_starts())


async def test_un_ticket_tenu_par_la_plateforme_est_decouvert(setup: Fixture) -> None:
    """Avec `tracker: internal`, il n'y a pas de dehors où lister des candidats.

    Livré le 2026-09-23, ce connecteur rendait `[]` : un ticket créé par la plateforme —
    un finding promu, une demande saisie dans le front — n'était jamais découvert et
    restait dans son état initial pour toujours. Sur le banc du 2026-09-23, les deux
    tickets issus des findings sont restés en `inbox` du début à la fin.
    """
    from choregos_adapters.tracker.interne import InternalTracker
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import cle_de_ticket_interne, project_bundle
    from sqlalchemy import select

    clear_fake_signals()
    setup.adapters.tracker = InternalTracker()

    async with session_scope() as session:
        bundle = await project_bundle(session, setup.project_slug)
        cle = await cle_de_ticket_interne(session, bundle)
        assert cle.startswith("BILLING-API-"), cle
        session.add(
            WorkItem(
                project_id=setup.project_id,
                tracker_key=cle,
                title="Base de profils candidats manquante",
                state=bundle.workflow.initial_state,
            )
        )

    result = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})
    assert cle in result["started"], result

    async with session_scope() as session:
        row = (await session.execute(select(WorkItem).where(WorkItem.tracker_key == cle))).scalar_one()
        assert row.temporal_wf_id, "le ticket de la plateforme porte l'identifiant de son workflow"


async def test_la_cle_interne_suit_le_projet_et_ne_se_repete_pas(setup: Fixture) -> None:
    """La clé rendue était le TITRE du ticket : illisible sur un board, et en collision
    avec `(project_id, tracker_key)` dès que deux findings se ressemblaient."""
    from choregos_api.db.models import Connector, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.base import cle_de_ticket_interne, project_bundle

    async with session_scope() as session:
        bundle = await project_bundle(session, setup.project_slug)
        premiere = await cle_de_ticket_interne(session, bundle)
        session.add(WorkItem(project_id=setup.project_id, tracker_key=premiere, title="a", state="inbox"))
        await session.flush()
        assert await cle_de_ticket_interne(session, bundle) != premiere

        # Le préfixe se configure : un métier veut `RH-7`, pas `STAFFING-7`.
        session.add(
            Connector(
                project_id=setup.project_id, kind="tracker", type="internal", config={"key_prefix": "RH"}
            )
        )
        await session.flush()
        assert (await cle_de_ticket_interne(session, bundle)).startswith("RH-")


async def test_une_passe_qui_echoue_ne_tue_pas_la_boucle(temporal_env: Any) -> None:
    """Le 08/10, sur le locataire dev, trois échecs sans App GitHub avaient laissé `reconcile-dev`
    mort pour de bon : plus aucune issue `agent-ready` n'était lue (S22-06). Une passe qui échoue
    est dite dans le statut, et la suivante repasse."""
    import uuid

    from choregos_orchestrator.workflows.reconciliation import TrackerReconciliation
    from temporalio import activity
    from temporalio.worker import Worker

    appels: list[str] = []

    @activity.defn(name="reconcile_tracker")
    async def rattrapage_capricieux(payload: dict[str, Any]) -> dict[str, Any]:
        appels.append(payload["project_slug"])
        if len(appels) <= 3:  # les trois tentatives de la première passe
            raise RuntimeError("[github] aucune authentification configurée")
        return {"created": ["DEMO-1"], "started": ["DEMO-1"]}

    client = temporal_env.client
    async with (
        Worker(client, task_queue="tracker", activities=[rattrapage_capricieux]),
        Worker(client, task_queue="test", workflows=[TrackerReconciliation]),
    ):
        poignee = await client.start_workflow(
            "TrackerReconciliation",
            {"project_slug": "dev", "interval_seconds": 1},
            id=f"reconcile-{uuid.uuid4()}",
            task_queue="test",
        )
        statut: dict[str, Any] = {}
        for _ in range(200):
            statut = await poignee.query("status")
            if statut["passes"] >= 2:
                break
            await temporal_env.sleep(1)
        assert statut["passes"] >= 2, statut
        assert statut["last"] == {"created": ["DEMO-1"], "started": ["DEMO-1"]}, statut
        await poignee.signal("stop")
        assert (await poignee.result())["stopped"] is True


async def test_le_demarrage_relance_une_boucle_morte_mais_jamais_un_ticket(
    setup: Fixture, monkeypatch: Any
) -> None:
    """Au démarrage du worker, une boucle de rattrapage morte en échec repart ; un ticket, lui, ne se
    démarre jamais deux fois (S22-06)."""
    from choregos_orchestrator import worker

    demandes: list[tuple[str, bool]] = []

    async def capter(
        name: str, workflow_id: str, payload: dict[str, Any], *, relancer_si_echoue: bool = False
    ) -> bool:
        demandes.append((workflow_id, relancer_si_echoue))
        return True

    monkeypatch.setattr("choregos_orchestrator.train_client.start_workflow_once", capter)
    await worker.start_reconciliation_loops()
    assert demandes and all(relance for _, relance in demandes), demandes

    import inspect

    from choregos_orchestrator.train_client import start_workflow_once

    assert inspect.signature(start_workflow_once).parameters["relancer_si_echoue"].default is False


async def _set_project(setup: Fixture, **fields: Any) -> None:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        for name, value in fields.items():
            setattr(project, name, value)


@pytest.mark.parametrize("status", ["archived", "suspended"])
async def test_an_archived_or_suspended_project_takes_nothing_on(setup: Fixture, status: str) -> None:
    """Should the loop's `stop` have missed, a pass on an archived or suspended project starts nothing."""
    clear_fake_signals()
    _seed_candidate(setup, "Waiting while the project is retired")
    await _set_project(setup, status=status)

    result = await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})

    assert result == {"skipped": status, "created": [], "started": []}
    assert fake_starts() == {}


async def test_the_catch_up_loop_of_a_project_starts_with_the_configured_interval(
    setup: Fixture, monkeypatch: Any
) -> None:
    from choregos_orchestrator.config import get_settings
    from choregos_orchestrator.train_client import start_reconciliation_loop

    clear_fake_signals()
    monkeypatch.setattr(get_settings(), "reconcile_interval_seconds", 45)

    assert await start_reconciliation_loop("billing") is True
    assert await start_reconciliation_loop("billing") is False, "one loop per project"
    assert fake_starts() == {"reconcile-billing": {"project_slug": "billing", "interval_seconds": 45}}

    monkeypatch.setattr(get_settings(), "reconcile_interval_seconds", 0)
    assert await start_reconciliation_loop("quiet") is False, "0 disables the catch-up"
    assert "reconcile-quiet" not in fake_starts()


@pytest.mark.parametrize("failed", [False, True])
async def test_a_project_activated_by_its_provisioning_is_polled_at_once(
    setup: Fixture, failed: bool
) -> None:
    """Before, only the worker's start polled the active projects: one activated later waited for a
    restart, and `candidates_jql` took nothing on (2026-11-02)."""
    from choregos_orchestrator.activities.provisioning import finish_provisioning

    clear_fake_signals()
    step = {"name": "github.ensure_labels", "status": "failed" if failed else "succeeded"}
    await _set_project(setup, status="provisioning", provision_state={"status": "running", "steps": [step]})

    await finish_provisioning({"project_id": setup.project_id})

    loop = f"reconcile-{setup.project_slug}"
    if failed:
        assert loop not in fake_starts(), "a failed provisioning leaves a draft: nothing to poll"
    else:
        assert fake_starts()[loop]["project_slug"] == setup.project_slug
