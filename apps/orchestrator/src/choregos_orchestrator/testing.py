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
from typing import TYPE_CHECKING, Any

from .repartition import ACTIVITES_PAR_FILE, file_de, nom_de_l_activite

if TYPE_CHECKING:
    from temporalio.api.enums.v1 import EventType
    from temporalio.client import WorkflowHandle


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


#: The events that close a run: once one is in the history, no other event will follow.
_CLOSED = (
    "EVENT_TYPE_WORKFLOW_EXECUTION_COMPLETED",
    "EVENT_TYPE_WORKFLOW_EXECUTION_FAILED",
    "EVENT_TYPE_WORKFLOW_EXECUTION_TIMED_OUT",
    "EVENT_TYPE_WORKFLOW_EXECUTION_CANCELED",
    "EVENT_TYPE_WORKFLOW_EXECUTION_TERMINATED",
    "EVENT_TYPE_WORKFLOW_EXECUTION_CONTINUED_AS_NEW",
)


async def wait_for_event(
    handle: WorkflowHandle[Any, Any],
    event_type: EventType.ValueType,
    *,
    count: int = 1,
    seconds: float = 30.0,
) -> None:
    """Return once the workflow's history holds `count` events of `event_type`.

    A signal sent straight after `start_workflow` can reach the server before the workflow's first
    task has run: it then lands in the first activation, before `run` starts, and a loop that a
    `stop` signal ends never runs a cycle. A test that signals a running loop first waits here for
    the event that proves the loop is running (the timer it waits on, the activity it scheduled).
    Polling the history does not unlock the time-skipping test server's clock: only awaiting a
    result does, so a timer the loop waits on does not fire while the test waits.

    Fails with an `AssertionError` after `seconds`, or as soon as the run is closed.
    """
    import asyncio

    from temporalio.api.enums.v1 import EventType

    wanted = EventType.Name(event_type)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while True:
        seen = [EventType.Name(event.event_type) for event in (await handle.fetch_history()).events]
        if seen.count(wanted) >= count:
            return
        closed = bool(seen) and seen[-1] in _CLOSED
        if closed or loop.time() >= deadline:
            why = "the run closed" if closed else f"{seconds} s"
            raise AssertionError(
                f"{count} x {wanted} expected in {handle.id}'s history, "
                f"{seen.count(wanted)} after {why}; seen: {seen}"
            )
        await asyncio.sleep(0.05)
