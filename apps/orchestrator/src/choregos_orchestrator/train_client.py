# SPDX-License-Identifier: Apache-2.0
"""Sending signals to Temporal workflows from an activity (train, findings, memory).

In fakes mode, the signals are queued in memory: the demo and the tests work without a Temporal
server, and the content of the signals stays checkable.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .config import get_settings

_FAKE_SIGNALS: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
_FAKE_STARTS: dict[str, dict[str, Any]] = {}
_FAKE_RESTARTS: list[str] = []


def fake_signals(workflow_id: str | None = None) -> Any:
    if workflow_id is None:
        return dict(_FAKE_SIGNALS)
    return list(_FAKE_SIGNALS.get(workflow_id, []))


def fake_starts() -> dict[str, dict[str, Any]]:
    return dict(_FAKE_STARTS)


def fake_restarts() -> list[str]:
    """The ids `restart_interpreter` started a new pass under, in order (C1)."""
    return list(_FAKE_RESTARTS)


def clear_fake_signals() -> None:
    _FAKE_SIGNALS.clear()
    _FAKE_STARTS.clear()
    _FAKE_RESTARTS.clear()


async def start_workflow_once(
    name: str,
    workflow_id: str,
    payload: dict[str, Any],
    *,
    relancer_si_echoue: bool = False,  # lang: fr-ok (the keyword test_reconciliation pins)
) -> bool:
    """Start a workflow unless one already exists under this id.

    Returns `True` if this start created the workflow. A deterministic id makes the gesture
    replayable: neither the periodic catch-up nor a replayed webhook can run the same ticket twice.
    """
    settings = get_settings()
    if settings.fakes:
        if workflow_id in _FAKE_STARTS:
            return False
        _FAKE_STARTS[workflow_id] = payload
        return True
    from temporalio.client import Client
    from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
    from temporalio.exceptions import WorkflowAlreadyStartedError

    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    try:
        await client.start_workflow(
            name,
            payload,
            id=workflow_id,
            task_queue="orchestrator",
            # Already finished for this ticket: it is not started again. Already running: the
            # existing one is taken over rather than creating a second.
            # A loop (a project's catch-up) that died in failure restarts; a ticket never does.
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE_FAILED_ONLY
            if relancer_si_echoue  # lang: fr-ok (the keyword test_reconciliation pins)
            else WorkflowIDReusePolicy.REJECT_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
    except WorkflowAlreadyStartedError:
        return False
    return True


async def restart_interpreter(workflow_id: str, payload: dict[str, Any]) -> None:
    """A new pass of an ENDED work item (C1): the next run of its interpreter id, even after a completed
    one (`ALLOW_DUPLICATE`). Called only once `services.recurrence.reopen_work_item` claimed the pass,
    so a second caller never gets here. A run still finishing is past its close-out: terminated."""
    settings = get_settings()
    if settings.fakes:
        _FAKE_STARTS[workflow_id] = payload
        _FAKE_RESTARTS.append(workflow_id)
        return
    from temporalio.client import Client
    from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy

    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    await client.start_workflow(
        "WorkflowInterpreter",
        payload,
        id=workflow_id,
        task_queue="orchestrator",
        id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        id_conflict_policy=WorkflowIDConflictPolicy.TERMINATE_EXISTING,
    )


async def start_reconciliation_loop(project_slug: str) -> bool:
    """Start a project's tracker catch-up loop (`reconcile-<slug>`); `False` if one already runs.

    Called when the worker starts, for every active project, and when a project becomes active:
    before, a project activated after the worker had started was never polled until its restart.
    """
    settings = get_settings()
    if settings.reconcile_interval_seconds <= 0:
        return False
    return await start_workflow_once(
        "TrackerReconciliation",
        f"reconcile-{project_slug}",
        {"project_slug": project_slug, "interval_seconds": settings.reconcile_interval_seconds},
        # A loop that died in failure restarts when the worker starts (S22-06).
        relancer_si_echoue=True,  # lang: fr-ok (the keyword test_reconciliation pins)
    )


async def start_memory_loop(project_slug: str) -> bool:
    """Start a project's memory loop (`mem-<slug>`, `MemoryIngestion`); `False` if one already runs.

    Nothing started it before S51-02: the memory was never fed, and the API's `reimport` and alert
    signals went to a workflow that did not exist. Called when the worker starts, for every active
    project whose policy keeps memory, and when a project becomes active. The payload names no `org`:
    the weekly A/B report is the organisation's, and one loop per project would post it once per project.
    """
    return await start_workflow_once(
        "MemoryIngestion",
        f"mem-{project_slug}",
        {"project_slug": project_slug},
        # A loop that died in failure restarts when the worker starts, like the tracker catch-up.
        relancer_si_echoue=True,  # lang: fr-ok (the keyword test_reconciliation pins)
    )


async def signal_workflow(
    workflow_id: str, name: str, payload: dict[str, Any], *, start: str | None = None
) -> None:
    """Signal a workflow; start it if `start` gives its type and it does not exist."""
    settings = get_settings()
    if settings.fakes:
        _FAKE_SIGNALS[workflow_id].append((name, payload))
        return
    from temporalio.client import Client

    client = await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)
    if start:
        await client.start_workflow(
            start,
            payload.get("start_payload", payload),
            id=workflow_id,
            task_queue="orchestrator",
            start_signal=name,
            start_signal_args=[payload],
        )
        return
    await client.get_workflow_handle(workflow_id).signal(name, payload)


async def signal_release_train(project_slug: str, env: str, name: str, payload: dict[str, Any]) -> None:
    await signal_workflow(
        f"train-{project_slug}-{env}",
        name,
        {**payload, "start_payload": {"project_slug": project_slug, "env": env}},
        start="ReleaseTrain",
    )


async def signal_findings(project_slug: str, name: str, payload: dict[str, Any]) -> None:
    await signal_workflow(
        f"findings-{project_slug}",
        name,
        {**payload, "start_payload": {"project_slug": project_slug}},
        start="FindingsTriage",
    )
