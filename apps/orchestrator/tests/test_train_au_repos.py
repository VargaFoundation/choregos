"""Le train au repos se relaie avant que Temporal ne le tue (S22-24).

Sur le locataire dev, le 10/10 : `train-dev-staging` portait 15 814 événements après 23 h sans un seul
départ. `_collect` bouclait — une activité `check_window` et un timer par tour —, et le seul
`continue_as_new` suivait un départ. À 51 200 événements, Temporal termine le workflow : le train
meurt, et les tickets qui y montent attendent pour toujours.

Ici, un VRAI `ReleaseTrain` attend devant une fenêtre fermée ; le seuil de relais est abaissé par son
entrée (`seuil_de_relais`) pour ne pas produire des milliers d'événements.
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
SEUIL = 60


def _doublures() -> dict[str, Any]:
    """Un train `release_train` devant une fenêtre fermée : rien ne part, le train boucle."""
    from temporalio import activity

    @activity.defn(name="load_train_config")
    async def load_train_config(payload: dict[str, Any]) -> dict[str, Any]:
        return {"mode": "release_train", "batch_min": 1, "batch_max": 8}

    @activity.defn(name="check_window")
    async def check_window(payload: dict[str, Any]) -> dict[str, Any]:
        return {"open": False, "due": False, "wait_seconds": 60, "next_departure": None}

    return {"load_train_config": load_train_config, "check_window": check_window}


async def _premier_run_se_relaie(handle: Any) -> str:
    """Attend la fin du PREMIER run : un relais, pas un échec ni une boucle sans fin."""
    from temporalio.client import WorkflowContinuedAsNewError

    with pytest.raises(WorkflowContinuedAsNewError) as relais:
        # Sans le correctif, ce run ne finit jamais : l'attente expire et le test rougit.
        await asyncio.wait_for(handle.result(follow_runs=False), timeout=60)
    return str(relais.value.new_execution_run_id)


async def test_un_train_vide_au_repos_se_relaie(temporal_env: Any, worker_factory: Any) -> None:
    async with worker_factory(remplacements=_doublures()):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "repos", "env": "staging", "seuil_de_relais": SEUIL},
            id="train-repos-staging",
            task_queue="test",
        )
        nouveau_run = await _premier_run_se_relaie(train)
        assert nouveau_run != train.result_run_id

        suite = temporal_env.client.get_workflow_handle("train-repos-staging")
        description = await suite.describe()
        assert description.run_id == nouveau_run
        etat = await suite.query("status_query")
        assert etat["status"] == "collecting" and etat["batch_size"] == 0, etat
        await suite.signal("abort", {"by": "test"})
        await suite.result()


async def test_un_lot_qui_attend_sa_fenetre_passe_au_run_suivant(
    temporal_env: Any, worker_factory: Any
) -> None:
    """Le lot n'est pas perdu au relais : il attend sa fenêtre dans le run suivant, numéro compris."""
    async with worker_factory(remplacements=_doublures()):
        train = await temporal_env.client.start_workflow(
            "ReleaseTrain",
            {"project_slug": "repos", "env": "prod", "batch_no": 3, "seuil_de_relais": SEUIL},
            id="train-repos-prod",
            task_queue="test",
        )
        await train.signal("merged", {"work_item_key": "varga/billing-api#7", "sha": "f7"})
        nouveau_run = await _premier_run_se_relaie(train)
        premier_run = temporal_env.client.get_workflow_handle("train-repos-prod", run_id=train.result_run_id)
        historique = await premier_run.fetch_history()

        suite = temporal_env.client.get_workflow_handle("train-repos-prod", run_id=nouveau_run)
        etat = await suite.query("status_query")
        assert etat["status"] == "collecting", etat
        assert etat["pending_items"] == ["varga/billing-api#7"], "le relais a perdu le lot"
        await suite.signal("abort", {"by": "test"})
        await suite.result()

    relais = historique.events[-1]
    assert relais.HasField("workflow_execution_continued_as_new_event_attributes")
    entree = json.loads(relais.workflow_execution_continued_as_new_event_attributes.input.payloads[0].data)
    assert entree["carried_items"] == [{"work_item_key": "varga/billing-api#7", "sha": "f7"}]
    assert entree["batch_no"] == 3 and entree["seuil_de_relais"] == SEUIL

    # L'historique du relais rejoue contre le code courant — et s'archive pour les suivants.
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(historique)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        cible = HISTORIQUES / "train-au-repos-S22-24.json"
        cible.write_text(json.dumps(json.loads(historique.to_json()), indent=1) + "\n", encoding="utf-8")
