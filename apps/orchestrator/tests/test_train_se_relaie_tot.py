"""Le train se relaie avant que son historique ne soit trop long à rejouer (S22-27).

Sur le locataire dev, le 10/10 (0.17.11) : des historiques de ~16 000 événements (2,6 Mo) prenaient
32 à 34 s à rejouer sur le pod de l'orchestrateur, quand la tâche de workflow expire à 10 s. Après un
`temporal workflow reset` ou un redémarrage du worker, chaque tâche expirait et chaque `status_query`
coûtait un rejeu complet (API 500). Le relais de S22-24 venait à 15 000 événements : trop tard.

Ici, de VRAIS `ReleaseTrain` : au repos avec le seuil PAR DÉFAUT, après un départ, après une synchro
`auto_sync` ; et un run d'avant S22-27 rejoué contre le seuil abaissé.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

HISTORIQUES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
#: Ce qu'un historique peut porter en restant rejouable bien en deçà des 10 s de la tâche de workflow
#: (~2 ms par événement sur le pod, mesuré le 10/10).
REJOUABLE = 2_500


def _au_repos() -> dict[str, Any]:
    """Un train `release_train` devant une fenêtre fermée : rien ne part, le train boucle."""
    from temporalio import activity

    @activity.defn(name="load_train_config")
    async def load_train_config(payload: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "release_train", "batch_min": 1, "batch_max": 8}

    @activity.defn(name="check_window")
    async def check_window(payload: dict[str, Any]) -> dict[str, Any]:
        return {"open": False, "due": False, "wait_seconds": 60, "next_departure": None}

    return {"load_train_config": load_train_config, "check_window": check_window}


def _qui_part() -> dict[str, Any]:
    """Un train dont la fenêtre est ouverte et dont chaque étape du départ réussit."""
    from temporalio import activity

    @activity.defn(name="load_train_config")
    async def load_train_config(payload: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "release_train", "batch_min": 1, "batch_max": 8, "soak_minutes": 1}

    @activity.defn(name="check_window")
    async def check_window(payload: dict[str, Any]) -> dict[str, Any]:
        return {"open": True, "due": True, "wait_seconds": 60, "next_departure": None}

    @activity.defn(name="create_release")
    async def create_release(payload: dict[str, Any]) -> dict[str, Any]:
        return {"release_id": f"rel-{payload['batch_no']}"}

    def _reussie(nom: str) -> Any:
        @activity.defn(name=nom)
        async def etape(payload: dict[str, Any]) -> dict[str, Any]:
            return {"ok": True}

        return etape

    etapes = ["promote", "run_smoke", "soak", "apply_terraform", "promote_canary_step", "verify_prod"]
    return {
        "load_train_config": load_train_config,
        "check_window": check_window,
        "create_release": create_release,
        **{nom: _reussie(nom) for nom in [*etapes, "finish_release"]},
    }


def _en_auto_sync() -> dict[str, Any]:
    """Un environnement `auto_sync` sain : chaque ticket est confirmé au premier regard."""
    from temporalio import activity

    @activity.defn(name="load_train_config")
    async def load_train_config(payload: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "auto_sync"}

    @activity.defn(name="confirmer_auto_sync")
    async def confirmer_auto_sync(payload: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    return {"load_train_config": load_train_config, "confirmer_auto_sync": confirmer_auto_sync}


async def _premier_run_se_relaie(handle: Any, timeout: float = 60) -> str:
    """Attend la fin du PREMIER run : un relais, pas un échec ni une attente sans fin."""
    from temporalio.client import WorkflowContinuedAsNewError

    with pytest.raises(WorkflowContinuedAsNewError) as relais:
        await asyncio.wait_for(handle.result(follow_runs=False), timeout=timeout)
    return str(relais.value.new_execution_run_id)


def _marqueurs(historique: Any) -> list[str]:
    """Les `patched` enregistrés dans l'historique, dans l'ordre."""
    from temporalio.api.common.v1 import Payload

    ids: list[str] = []
    for evenement in historique.events:
        if evenement.HasField("marker_recorded_event_attributes"):
            details = evenement.marker_recorded_event_attributes.details
            if "patch-data" in details:
                payload: Payload = details["patch-data"].payloads[0]
                ids.append(json.loads(payload.data)["id"])
    return ids


def _activites(historique: Any) -> list[str]:
    return [
        e.activity_task_scheduled_event_attributes.activity_type.name
        for e in historique.events
        if e.HasField("activity_task_scheduled_event_attributes")
    ]


def _entree_du_relais(historique: Any) -> dict[str, Any]:
    relais = historique.events[-1]
    assert relais.HasField("workflow_execution_continued_as_new_event_attributes")
    donnees = relais.workflow_execution_continued_as_new_event_attributes.input.payloads[0].data
    entree: dict[str, Any] = json.loads(donnees)
    return entree


async def test_un_train_au_repos_se_relaie_avant_d_etre_trop_long_a_rejouer(
    temporal_env: Any, worker_factory: Any
) -> None:
    """Le seuil PAR DÉFAUT : aucun `seuil_de_relais` dans l'entrée, comme les trains du locataire."""
    from choregos_orchestrator.workflows.train import LE_TRAIN_AU_REPOS_SE_RELAIE, LE_TRAIN_SE_RELAIE_TOT

    async with worker_factory(remplacements=_au_repos()):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "repos", "env": "staging"},
            id="train-repos-court",
            task_queue="test",
        )
        # Sans le correctif, le relais attend 15 000 événements : l'attente expire, ou l'historique
        # dépasse ce qu'une tâche de workflow sait rejouer.
        nouveau_run = await _premier_run_se_relaie(train)
        premier = temporal_env.client.get_workflow_handle("train-repos-court", run_id=train.result_run_id)
        historique = await premier.fetch_history()
        suite = temporal_env.client.get_workflow_handle("train-repos-court", run_id=nouveau_run)
        assert (await suite.query("status_query"))["status"] == "collecting"
        await suite.signal("abort", {"by": "test"})
        await suite.result()

    assert len(historique.events) <= REJOUABLE, f"relayé à {len(historique.events)} événements"
    marqueurs = _marqueurs(historique)
    assert LE_TRAIN_SE_RELAIE_TOT in marqueurs and LE_TRAIN_AU_REPOS_SE_RELAIE not in marqueurs, marqueurs
    assert "seuil_de_relais" not in _entree_du_relais(historique)


async def test_un_train_se_relaie_juste_apres_son_depart(temporal_env: Any, worker_factory: Any) -> None:
    """Un départ ajoute des dizaines d'événements : passé le seuil, le train se relaie aussitôt, sans
    attendre de repasser par la collecte (où le relais au repos l'aurait fait avant S22-27)."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from choregos_orchestrator.workflows.train import LE_TRAIN_SE_RELAIE_TOT
    from temporalio.worker import Replayer

    async with worker_factory(remplacements=_qui_part()):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "depart", "env": "prod", "seuil_de_relais": 40},
            id="train-depart-prod",
            task_queue="test",
            start_signal="merged",
            start_signal_args=[{"work_item_key": "varga/billing-api#9", "sha": "a9"}],
        )
        nouveau_run = await _premier_run_se_relaie(train)
        premier = temporal_env.client.get_workflow_handle("train-depart-prod", run_id=train.result_run_id)
        historique = await premier.fetch_history()
        suite = temporal_env.client.get_workflow_handle("train-depart-prod", run_id=nouveau_run)
        await suite.signal("abort", {"by": "test"})
        await suite.result()

    activites = _activites(historique)
    assert activites.count("check_window") == 1, activites
    assert activites[-1] == "finish_release", "le relais suit le départ, pas une nouvelle collecte"
    assert LE_TRAIN_SE_RELAIE_TOT in _marqueurs(historique)
    entree = _entree_du_relais(historique)
    assert entree["carried_items"] == [] and entree["batch_no"] == 2

    # L'historique du relais rejoue contre le code courant — et s'archive pour les suivants.
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(historique)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        cible = HISTORIQUES / "train-se-relaie-tot-S22-27.json"
        cible.write_text(json.dumps(json.loads(historique.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_une_synchro_auto_sync_se_relaie(temporal_env: Any, worker_factory: Any) -> None:
    from choregos_orchestrator.workflows.train import LE_TRAIN_SE_RELAIE_TOT

    async with worker_factory(remplacements=_en_auto_sync()):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "synchro", "env": "dev", "seuil_de_relais": 10},
            id="train-synchro-dev",
            task_queue="test",
            start_signal="merged",
            start_signal_args=[{"work_item_key": "varga/billing-api#10", "sha": "b10"}],
        )
        # Sans le correctif, la synchro ne se relaie qu'à 15 000 : le run attend un ticket, sans fin.
        nouveau_run = await _premier_run_se_relaie(train, timeout=30)
        premier = temporal_env.client.get_workflow_handle("train-synchro-dev", run_id=train.result_run_id)
        historique = await premier.fetch_history()
        suite = temporal_env.client.get_workflow_handle("train-synchro-dev", run_id=nouveau_run)
        for _ in range(100):  # le run suivant relit sa configuration avant de se dire `auto_sync`
            etat = await suite.query("status_query")
            if etat["status"] == "auto_sync":
                break
            await asyncio.sleep(0.1)
        assert etat["status"] == "auto_sync" and etat["batch_size"] == 0, etat
        await suite.signal("abort", {"by": "test"})
        await suite.result()

    assert _activites(historique)[-1] == "confirmer_auto_sync"
    assert LE_TRAIN_SE_RELAIE_TOT in _marqueurs(historique)
    assert _entree_du_relais(historique)["batch_no"] == 2


async def test_un_run_d_avant_rejoue_contre_le_seuil_abaisse(
    temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un run d'avant S22-27, relayé au repos au seuil d'avant, rejoue contre le seuil abaissé.

    À l'échelle : le seuil d'avant à 300, le nouveau à 100. Le run « d'avant » est enregistré avec un
    nouveau seuil hors d'atteinte — il se relaie donc à 300 sous l'ancien marqueur, comme 0.17.11 à
    15 000. Puis on montre que ce même historique ne rejouerait PAS si l'on avait seulement abaissé le
    seuil, sans nouveau marqueur : c'est ce qui a imposé `LE_TRAIN_SE_RELAIE_TOT`."""
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from choregos_orchestrator.workflows import train as module
    from temporalio.worker import Replayer

    monkeypatch.setattr(module, "SEUIL_DE_RELAIS", 10**9)
    monkeypatch.setattr(module, "SEUIL_DE_RELAIS_D_AVANT", 300)
    async with worker_factory(remplacements=_au_repos()):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "avant", "env": "staging"},
            id="train-avant-staging",
            task_queue="test",
        )
        nouveau_run = await _premier_run_se_relaie(train)
        premier = temporal_env.client.get_workflow_handle("train-avant-staging", run_id=train.result_run_id)
        historique = await premier.fetch_history()
        suite = temporal_env.client.get_workflow_handle("train-avant-staging", run_id=nouveau_run)
        await suite.signal("abort", {"by": "test"})
        await suite.result()
    assert 300 < len(historique.events) < 400
    marqueurs = _marqueurs(historique)
    assert module.LE_TRAIN_AU_REPOS_SE_RELAIE in marqueurs and module.LE_TRAIN_SE_RELAIE_TOT not in marqueurs

    # Le code d'aujourd'hui, à l'échelle : le nouveau marqueur est interrogé dès 100, rend `False`, et
    # le run garde son relais d'avant.
    monkeypatch.setattr(module, "SEUIL_DE_RELAIS", 100)
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(historique)

    # Le seuil seulement abaissé (l'ancien marqueur interrogé dès 100) : écart de déterminisme.
    monkeypatch.setattr(module, "SEUIL_DE_RELAIS_D_AVANT", 100)
    rejeu = await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(historique, raise_on_replay_failure=False)
    assert rejeu.replay_failure is not None
    assert "TMPRL1100" in str(rejeu.replay_failure)
