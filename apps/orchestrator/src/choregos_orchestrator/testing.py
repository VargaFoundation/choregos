# SPDX-License-Identifier: Apache-2.0
"""Outillage de test : des workers **répartis par file**, comme le chart en déploie un par file.

Ce module vit dans le paquet et non dans un `conftest.py` parce que **trois** suites en ont besoin
— `apps/orchestrator/tests`, `tests/e2e`, `tests/replay` — et qu'une copie par suite se
désynchronise en silence. C'était déjà le cas : les trois construisaient un worker unique portant
toutes les activités, donc aucune ne pouvait voir que les workflows planifiaient tout sur la même
file (défaut du 2026-09-27).

**Différence assumée avec la production** : ici, la file du workflow ne porte QUE les activités
sans file déclarée. En production, `orchestrator` porte tout — un filet pour ne pas laisser une
tâche orpheline pendant une montée de version. Le filet rendrait le test aveugle : une activité mal
routée y tournerait quand même. Ne pas le poser est ce qui fait échouer la suite quand le routage
régresse.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Iterable, Mapping
from typing import Any

from .repartition import ACTIVITES_PAR_FILE, file_de, nom_de_l_activite


def avec_doublures(activites: Iterable[Any], remplacements: Mapping[str, Any] | None = None) -> list[Any]:
    """La liste d'activités où celles nommées dans `remplacements` sont substituées.

    Les clés sont des **noms Temporal** (`@activity.defn(name=…)`), pas des noms de fonction : c'est
    le nom déclaré qui décide de la file, et c'est lui qui voyage dans l'historique.
    """
    remplacements = dict(remplacements or {})
    sortie: list[Any] = []
    vus: set[str] = set()
    for activite in activites:
        nom = nom_de_l_activite(activite)
        if nom is not None and nom in remplacements:
            sortie.append(remplacements[nom])
            vus.add(nom)
        else:
            sortie.append(activite)
    # Une doublure d'activité que le paquet ne connaît pas reste enregistrée.
    sortie.extend(doublure for nom, doublure in remplacements.items() if nom not in vus)
    return sortie


def repartir(activites: Iterable[Any], file_du_workflow: str) -> dict[str, list[Any]]:
    """Groupe des activités par file. Celles sans file déclarée vont sur la file du workflow."""
    par_file: dict[str, list[Any]] = {file_du_workflow: []}
    for file in ACTIVITES_PAR_FILE:
        par_file.setdefault(file, [])
    for activite in activites:
        par_file[file_de(activite) or file_du_workflow].append(activite)
    return par_file


@contextlib.asynccontextmanager
async def workers_repartis(
    client: Any,
    *,
    activites: Iterable[Any],
    workflows: Iterable[Any],
    file_du_workflow: str = "test",
    remplacements: Mapping[str, Any] | None = None,
    **options: Any,
) -> AsyncIterator[dict[str, list[Any]]]:
    """Un worker par file, le temps du bloc. Rend la répartition, pour qu'un test puisse l'affirmer."""
    from temporalio.worker import Worker

    par_file = repartir(avec_doublures(activites, remplacements), file_du_workflow)
    portes = list(workflows)
    # Une file sans activité n'a pas de worker : Temporal refuse un worker vide, et un test qui
    # ne fournit qu'un sous-ensemble d'activités (les doublures de `tests/replay`) laisse des
    # files inoccupées. Ce qu'aucun worker ne porte, aucun workflow ne le planifie non plus.
    workers = [
        Worker(
            client,
            task_queue=file,
            workflows=portes if file == file_du_workflow else [],
            activities=portees,
            **options,
        )
        for file, portees in par_file.items()
        if portees or (file == file_du_workflow and portes)
    ]
    async with contextlib.AsyncExitStack() as pile:
        for worker in workers:
            await pile.enter_async_context(worker)
        yield par_file
