# SPDX-License-Identifier: Apache-2.0
"""Temporal workers: one deployment per task queue (docs/plan/02 §2.1)."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
from typing import Any

from choregos_api.logging import configure_logging, get_logger
from choregos_core import telemetry

from .activities import ALL_ACTIVITIES
from .config import ALL_QUEUES, get_settings
from .queues import ACTIVITIES_BY_QUEUE
from .workflows import ALL_WORKFLOWS, WORKFLOW_ACTIVITIES

logger = get_logger("choregos.worker")


def activities_for(queue: str) -> list[Any]:
    """The `orchestrator` queue carries everything; the others carry their specialty.

    The net is deliberate: the workflows now schedule each activity on its own queue
    (`workflows.scheduling`), but a task already waiting on `orchestrator` at the time of a version
    upgrade must not be left orphaned. The split is guaranteed by a test, not by an absence of
    registration.
    """
    everything = [*ALL_ACTIVITIES, *WORKFLOW_ACTIVITIES]
    if queue == "orchestrator":
        return everything
    names = ACTIVITIES_BY_QUEUE.get(queue, ())
    selected: list[Any] = []
    for candidate in everything:
        definition = getattr(candidate, "__temporal_activity_definition", None)
        if definition is not None and getattr(definition, "name", "") in names:
            selected.append(candidate)
    return selected


async def start_reconciliation_loops() -> list[str]:
    """One catch-up loop per active project (S3-06).

    The start is idempotent: restarting a worker does not create a second loop, and a project added
    afterwards is picked up at the next restart or by the API.
    """
    from choregos_api.services.projets import active_project_slugs

    from .train_client import start_reconciliation_loop

    settings = get_settings()
    if settings.reconcile_interval_seconds <= 0:
        logger.info("tracker catch-up disabled")
        return []
    slugs = await active_project_slugs()
    started: list[str] = []
    for slug in slugs:
        fresh = await start_reconciliation_loop(slug)
        if fresh:
            started.append(slug)
    logger.info("tracker catch-up", projects=len(slugs), started=len(started))
    return started


async def start_memory_loops() -> list[str]:
    """One memory loop per active project whose policy keeps memory (S51-02).

    Idempotent like the tracker loops: restarting a worker does not create a second loop, and a loop
    that died in failure starts again. A project whose policy says `memory.enabled: false` gets none.
    """
    from choregos_api.services.projets import active_project_slugs

    from .train_client import start_memory_loop

    slugs = await active_project_slugs(keeping_memory=True)
    started: list[str] = []
    for slug in slugs:
        if await start_memory_loop(slug):
            started.append(slug)
    logger.info("memory loops", projects=len(slugs), started=len(started))
    return started


async def start_knowledge_sync_loops() -> list[str]:
    """One sync loop per knowledge source (S33-04). Idempotent like the tracker loops: the API
    starts a source's loop when it creates it; the worker restarts the ones a failure ended."""
    from choregos_api.db.models import KnowledgeSource, Organization
    from choregos_api.db.session import session_scope
    from choregos_api.temporal import knowledge_sync_id
    from sqlalchemy import select

    from .train_client import start_workflow_once

    async with session_scope(orgs="*") as session:
        rows = (
            await session.execute(
                select(Organization.slug, KnowledgeSource.id, KnowledgeSource.interval_minutes).join(
                    Organization, Organization.id == KnowledgeSource.org_id
                )
            )
        ).tuples()
        sources = list(rows)
    started: list[str] = []
    for org, source_id, minutes in sources:
        if await start_workflow_once(
            "KnowledgeSync",
            knowledge_sync_id(source_id),
            {"org": org, "source_id": source_id, "interval_seconds": minutes * 60},
            relancer_si_echoue=True,  # lang: fr-ok (the keyword test_reconciliation pins)
        ):
            started.append(source_id)
    logger.info("knowledge sync loops", sources=len(sources), started=len(started))
    return started


async def run_worker(queues: list[str]) -> None:
    from choregos_adapters import charger_les_greffons
    from temporalio.client import Client
    from temporalio.worker import Worker

    # Extensions also declare EFFECTS of governed actions (ADR 0035): the API knows them in order
    # to propose, and the worker must know them in order to PLAY them. It did not load them, so an
    # extension's effect was "unknown" in the very place where it had to run (S20-08).
    charger_les_greffons()  # lang: fr-ok (renamed by S45-10)
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
    logger.info("workers started", queues=queues, address=settings.temporal_address)
    async with contextlib.AsyncExitStack() as stack:
        for worker in workers:
            await stack.enter_async_context(worker)
        if "orchestrator" in queues:
            await start_reconciliation_loops()
            await start_memory_loops()
            await start_knowledge_sync_loops()
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
    telemetry.configure_tracing("choregos-orchestrator")
    queues = [queue.strip() for queue in args.queues.split(",") if queue.strip()]
    try:
        asyncio.run(run_worker(queues))
    finally:
        telemetry.shutdown_tracing()


if __name__ == "__main__":
    main()
