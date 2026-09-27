"""La répartition des activités par file est-elle **appliquée**, ou seulement déclarée ?

Le 2026-09-27, sur le locataire dev de Diametral, un run est resté en `queued` et le ticket est
mort sur un `ConnectTimeout` vers l'API Kubernetes. La politique Cilium de la plateforme n'ouvre
l'API qu'aux pods portant `choregos/queue: executor` — la file qui, d'après `repartition`, crée les
Jobs. Mais `workflow.execute_activity` sans `task_queue` planifie sur la file du *workflow* :
`start_run` tournait donc sur `orchestrator`, dans un pod sans droit de sortie.

La table existait depuis le premier jour et personne ne pouvait le voir : les trois suites Temporal
montaient un worker unique portant **toutes** les activités, qui répond quelle que soit la file
demandée. Ces tests-là interrogent l'historique, seul endroit qui dise sur quelle file une activité
a réellement été planifiée.
"""

from __future__ import annotations

from typing import Any

import pytest

from .conftest import Fixture, stage_result
from .test_interpreter import scripted, start


async def _files_planifiees(handle: Any) -> tuple[dict[str, str], bool]:
    """Les files par activité, et si le workflow est déjà mort.

    Le second membre évite de scruter une minute un workflow qui a fini d'échouer : une activité
    planifiée sur une file que personne n'écoute tue le ticket en quelques secondes.
    """
    histoire = await handle.fetch_history()
    files: dict[str, str] = {}
    mort = False
    for evenement in histoire.events:
        attributs = evenement.activity_task_scheduled_event_attributes
        if attributs.activity_type.name:
            files[attributs.activity_type.name] = attributs.task_queue.name
        if evenement.HasField("workflow_execution_failed_event_attributes"):
            mort = True
    return files, mort


@pytest.mark.asyncio
async def test_l_historique_dit_sur_quelle_file_chaque_activite_est_planifiee(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """`start_run` sur `executor`, `mirror_state` sur `tracker`, `load_context` sur la file du workflow.

    C'est l'assertion dont dépend la politique réseau de Diametral. Sans le routage, les trois
    valeurs valent `test` — la file du workflow.

    L'historique porte la file dès la **planification**, avant qu'un worker ait pris la tâche :
    l'affirmation ne dépend donc pas du fait qu'un worker écoute, et un routage cassé se voit tout
    de suite au lieu d'expirer au bout d'une minute.
    """
    scripted(setup.adapters, stage_result("spec rédigée", outputs={"size": "M", "risk": "low"}))

    async with worker_factory():
        handle = await start(temporal_env, setup)

        files = await _attendre_planification(handle, "prepare_stage")
        assert files["prepare_stage"] == "executor", (
            "la préparation d'étape doit partir de `executor` : c'est la seule file à qui la "
            f"politique Cilium ouvre l'API Kubernetes (vu : {files['prepare_stage']!r})"
        )
        assert files.get("mirror_state") == "tracker", (
            f"les appels au tracker sont isolés et rate-limités (vu : {files.get('mirror_state')!r})"
        )
        assert files.get("load_context") == "test", (
            "une activité sans file déclarée reste sur la file du workflow, qui porte tout "
            f"(vu : {files.get('load_context')!r})"
        )

        # `start_run` — celle qui crée le Job Kubernetes — n'est planifiée qu'une fois l'étape
        # préparée : c'est elle qui expirait en `ConnectTimeout` sur le locataire.
        files = await _attendre_planification(handle, "start_run")
        assert files["start_run"] == "executor", f"vu : {files['start_run']!r}"


async def _attendre_planification(handle: Any, activite: str, *, timeout: int = 60) -> dict[str, str]:
    """Attend qu'une activité apparaisse dans l'historique, et rend les files vues."""
    import asyncio

    files: dict[str, str] = {}
    for _ in range(timeout * 10):
        files, mort = await _files_planifiees(handle)
        if activite in files:
            return files
        if mort:
            raise AssertionError(f"le workflow est mort avant de planifier {activite} — files vues : {files}")
        await asyncio.sleep(0.1)
    raise AssertionError(f"{activite} jamais planifiée (files vues : {files})")


def test_la_table_ne_nomme_que_des_activites_qui_existent() -> None:
    """Un renommage d'activité qui oublie la table la rendrait muette, sans rien casser.

    C'est le mode de panne le plus discret de ce mécanisme : l'activité cesse d'être routée, elle
    tourne sur la file du workflow, tout fonctionne — jusqu'au jour où une politique réseau compte
    sur la file déclarée.
    """
    from choregos_orchestrator.activities import ALL_ACTIVITIES
    from choregos_orchestrator.repartition import FILE_PAR_ACTIVITE, nom_de_l_activite
    from choregos_orchestrator.workflows import WORKFLOW_ACTIVITIES

    connues = {
        nom
        for activite in [*ALL_ACTIVITIES, *WORKFLOW_ACTIVITIES]
        if (nom := nom_de_l_activite(activite)) is not None
    }
    fantomes = sorted(set(FILE_PAR_ACTIVITE) - connues)
    assert not fantomes, f"la table route des activités qui n'existent plus : {fantomes}"


def test_un_appel_qui_precise_sa_file_garde_le_dernier_mot() -> None:
    """L'enveloppe pose un défaut ; elle ne confisque pas la décision de l'appelant."""
    from choregos_orchestrator.activities import stage as stage_activities
    from choregos_orchestrator.activities.interpretation import load_context
    from choregos_orchestrator.workflows import planification

    vus: list[dict[str, Any]] = []

    def faux_execute_activity(activite: Any, *args: Any, **options: Any) -> None:
        vus.append(options)

    original = planification.workflow.execute_activity
    planification.workflow.execute_activity = faux_execute_activity  # type: ignore[assignment]
    try:
        planification.executer_activite(stage_activities.start_run, {})
        planification.executer_activite(stage_activities.start_run, {}, task_queue="la-mienne")
        planification.executer_activite(load_context, {})
    finally:
        planification.workflow.execute_activity = original  # type: ignore[assignment]

    assert vus[0]["task_queue"] == "executor"
    assert vus[1]["task_queue"] == "la-mienne"
    assert "task_queue" not in vus[2], "une activité sans file déclarée ne doit pas en recevoir une"
