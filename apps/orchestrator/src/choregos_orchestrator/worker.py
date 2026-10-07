# SPDX-License-Identifier: Apache-2.0
"""Workers Temporal : un déploiement par task queue (docs/plan/02 §2.1)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from typing import Any

from choregos_api.logging import configure_logging, get_logger

from .activities import ALL_ACTIVITIES
from .config import ALL_QUEUES, get_settings
from .repartition import ACTIVITES_PAR_FILE
from .workflows import ALL_WORKFLOWS, WORKFLOW_ACTIVITIES

logger = get_logger("choregos.worker")


def activities_for(queue: str) -> list[Any]:
    """La queue `orchestrator` porte tout ; les autres portent leur spécialité.

    Le filet est volontaire : les workflows planifient désormais chaque activité sur sa file
    (`workflows.planification`), mais une tâche déjà en attente sur `orchestrator` au moment d'une
    montée de version ne doit pas rester orpheline. La répartition est garantie par un test, pas
    par une absence d'enregistrement.
    """
    everything = [*ALL_ACTIVITIES, *WORKFLOW_ACTIVITIES]
    if queue == "orchestrator":
        return everything
    names = ACTIVITES_PAR_FILE.get(queue, ())
    selected: list[Any] = []
    for candidate in everything:
        definition = getattr(candidate, "__temporal_activity_definition", None)
        if definition is not None and getattr(definition, "name", "") in names:
            selected.append(candidate)
    return selected


async def start_reconciliation_loops() -> list[str]:
    """Une boucle de rattrapage par projet actif (S3-06).

    Le démarrage est idempotent : redémarrer un worker ne crée pas une seconde boucle,
    et un projet ajouté après coup est pris au prochain redémarrage ou par l'API.
    """
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    from .train_client import start_workflow_once

    settings = get_settings()
    if settings.reconcile_interval_seconds <= 0:
        logger.info("rattrapage tracker désactivé")
        return []
    async with session_scope(orgs="*") as session:
        slugs = (
            (await session.execute(select(Project.slug).where(Project.status == "active"))).scalars().all()
        )
    started: list[str] = []
    for slug in slugs:
        fresh = await start_workflow_once(
            "TrackerReconciliation",
            f"reconcile-{slug}",
            {"project_slug": slug, "interval_seconds": settings.reconcile_interval_seconds},
        )
        if fresh:
            started.append(slug)
    logger.info("rattrapage tracker", projets=len(slugs), demarres=len(started))
    return started


async def run_worker(queues: list[str]) -> None:
    from choregos_adapters import charger_les_greffons
    from temporalio.client import Client
    from temporalio.worker import Worker

    # Les greffons déclarent aussi des EFFETS d'actions gouvernées (ADR 0035) : l'API les connaît
    # pour proposer, le worker doit les connaître pour les JOUER. Il ne les chargeait pas — un effet
    # de greffon était « inconnu » là même où il devait s'exécuter (S20-08).
    charger_les_greffons()
    settings = get_settings()
    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    workers = [
        Worker(
            client,
            task_queue=queue,
            workflows=ALL_WORKFLOWS if queue == "orchestrator" else [],
            activities=activities_for(queue),
            max_concurrent_activities=20,
        )
        for queue in queues
    ]
    logger.info("workers démarrés", queues=queues, address=settings.temporal_address)
    async with contextlib.AsyncExitStack() as stack:
        for worker in workers:
            await stack.enter_async_context(worker)
        if "orchestrator" in queues:
            await start_reconciliation_loops()
        await asyncio.Future()


def main() -> None:
    parser = argparse.ArgumentParser(description="Workers Temporal de Choregos")
    parser.add_argument(
        "--queues",
        default=",".join(ALL_QUEUES),
        help=f"comma-separated task queues (default: {','.join(ALL_QUEUES)})",
    )
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    queues = [queue.strip() for queue in args.queues.split(",") if queue.strip()]
    asyncio.run(run_worker(queues))


if __name__ == "__main__":
    main()
