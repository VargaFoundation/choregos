# SPDX-License-Identifier: Apache-2.0
"""Temporal client on the API side: starting workflows and routing signals.

The API knows only four gestures of Temporal: start a `WorkflowInterpreter`, send it a signal,
query it, and signal a train. In fakes mode, an in-memory client records the calls so that the
tests and the demo work without a server.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol

from choregos_contracts import Control, HumanDecision, InboundEvent
from choregos_core.compat import alias_names

from .config import get_settings
from .logging import get_logger

logger = get_logger("choregos.temporal")


def interpreter_id(project_slug: str, tracker_key: str) -> str:
    """Deterministic ID: one ticket cannot have two workflows (S1-10)."""
    safe = tracker_key.replace("/", "_").replace("#", "-")
    return f"wi-{project_slug}-{safe}"


def train_id(project_slug: str, env: str) -> str:
    return f"train-{project_slug}-{env}"


def findings_id(project_slug: str) -> str:
    return f"findings-{project_slug}"


def action_id(action: str) -> str:
    """The Temporal identifier of a governed action: a single workflow per action (ADR 0035)."""
    return f"action-{action}"


def trial_workflow_id(work_item_id: str) -> str:
    """The `AgentTrial` of a trial work item (S32-03): one per trial."""
    return f"trial-{work_item_id}"


def trial_run_id(work_item_id: str) -> str:
    """The run of a trial: `prepare_stage` names it `<work item>-<transition>-<attempt>`."""
    return f"{work_item_id}-trial-1"


def knowledge_sync_id(source_id: str) -> str:
    """One sync loop per knowledge source (S33-04)."""
    return f"knowledge-sync-{source_id}"


def knowledge_distill_id(base_id: str, started_at: str) -> str:
    """One distillation run per request (S42-10c): a finished workflow's id cannot be reused."""
    return f"knowledge-distill-{base_id}-{re.sub(r'[^0-9]', '', started_at)}"


def hosted_build_id(org_id: str, key: str, attempt: int) -> str:
    """One build per organisation, build key and attempt (S41-08): a second version with the same
    zip starts nothing, a failed build published again is the next attempt."""
    return f"hosted-build-{org_id}-{key.removeprefix('sha256:')}-{attempt}"


def session_pod_id(session_id: str) -> str:
    """One pod workflow per hosted session (S41-11): a second response starts nothing."""
    return f"agent-session-{session_id}"


def provisioning_id(project_slug: str) -> str:
    return f"prov-{project_slug}"


#: How long a read waits for a workflow query before answering "unknown" (S26-01). A train whose
#: history is long to replay can hold a query for tens of seconds, then fail with "query task not
#: found, or already expired"; a status page must not wait for that.
QUERY_TIMEOUT_S = 3.0


class TemporalGateway(Protocol):
    async def start_interpreter(self, workflow_id: str, payload: dict[str, Any]) -> str: ...
    async def signal(self, workflow_id: str, name: str, payload: Any) -> None: ...
    async def query(self, workflow_id: str, name: str) -> Any:
        """The query's answer, or None when the workflow does not exist or does not answer in time."""
        ...

    async def start_train(self, workflow_id: str, payload: dict[str, Any]) -> str: ...
    async def start_provisioning(self, workflow_id: str, payload: dict[str, Any]) -> str: ...
    async def start_action(self, workflow_id: str, payload: dict[str, Any]) -> str: ...
    async def start_trial(self, workflow_id: str, payload: dict[str, Any]) -> str: ...
    async def start_knowledge_sync(self, workflow_id: str, payload: dict[str, Any]) -> str: ...
    async def start_reconciliation(self, workflow_id: str, payload: dict[str, Any]) -> str: ...
    async def start_memory_ingestion(self, workflow_id: str, payload: dict[str, Any]) -> str: ...
    async def start_hosted_build(self, workflow_id: str, payload: dict[str, Any]) -> str: ...
    async def start_session_pod(self, workflow_id: str, payload: dict[str, Any]) -> str: ...
    async def start_knowledge_distill(self, workflow_id: str, payload: dict[str, Any]) -> str: ...
    async def cancel(self, workflow_id: str) -> None: ...
    async def describe(self, workflow_id: str) -> WorkflowState | None: ...


@dataclass
class WorkflowState:
    """What Temporal knows of a workflow: its status, and why if it is dead."""

    status: str  # RUNNING, COMPLETED, FAILED, CANCELED, TERMINATED, TIMED_OUT, CONTINUED_AS_NEW
    failure: str | None = None

    @property
    def dead(self) -> bool:
        return self.status in {"FAILED", "TERMINATED", "TIMED_OUT"}


@dataclass
class FakeTemporal:
    """In-memory client: records starts and signals, without a Temporal server."""

    started: dict[str, dict[str, Any]] = field(default_factory=dict)
    signals: list[tuple[str, str, Any]] = field(default_factory=list)
    queries: dict[str, Any] = field(default_factory=dict)
    cancelled: list[str] = field(default_factory=list)
    described: dict[str, WorkflowState] = field(default_factory=dict)
    #: workflows that no longer run: a signal to one is NOT_FOUND, as the real server answers (#679)
    gone: set[str] = field(default_factory=set)
    #: every `start_session_pod` call, a repeated id included
    start_calls: list[str] = field(default_factory=list)

    async def start_interpreter(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.started.setdefault(workflow_id, payload)  # deterministic ID => idempotent start
        state = self.described.get(workflow_id)
        if state is None or state.status != "COMPLETED":  # ALLOW_DUPLICATE_FAILED_ONLY, as the real one
            self.gone.discard(workflow_id)
        return workflow_id

    async def describe(self, workflow_id: str) -> WorkflowState | None:
        return self.described.get(workflow_id)

    async def signal(self, workflow_id: str, name: str, payload: Any) -> None:
        if workflow_id in self.gone:
            from temporalio.service import RPCError, RPCStatusCode

            raise RPCError("workflow execution already completed", RPCStatusCode.NOT_FOUND, b"")
        self.signals.append((workflow_id, name, payload))

    async def query(self, workflow_id: str, name: str) -> Any:
        return await answer_or_none(workflow_id, name, self._query(workflow_id, name))

    async def _query(self, workflow_id: str, name: str) -> Any:
        """Like the real client: a workflow nobody started is NOT_FOUND, not an empty answer."""
        from temporalio.service import RPCError, RPCStatusCode

        known = workflow_id in self.started or any(
            key.rsplit(":", 1)[0] == workflow_id for key in self.queries
        )
        if not known:
            raise RPCError(f"workflow not found for ID: {workflow_id}", RPCStatusCode.NOT_FOUND, b"")
        return self.queries.get(f"{workflow_id}:{name}")

    async def start_train(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.started.setdefault(workflow_id, payload)
        return workflow_id

    async def start_provisioning(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.started[workflow_id] = payload
        return workflow_id

    async def start_action(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.started.setdefault(workflow_id, payload)  # `action-<id>`: a single start
        return workflow_id

    async def start_trial(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.started.setdefault(workflow_id, payload)  # `trial-<work item>`: a single start
        return workflow_id

    async def start_knowledge_sync(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.started.setdefault(workflow_id, payload)  # one loop per source
        return workflow_id

    async def start_reconciliation(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.started.setdefault(workflow_id, payload)  # one catch-up loop per project
        return workflow_id

    async def start_memory_ingestion(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.started.setdefault(workflow_id, payload)  # one memory loop per project
        state = self.described.get(workflow_id)
        if state is None or state.status != "COMPLETED":  # ALLOW_DUPLICATE_FAILED_ONLY, as the real one
            self.gone.discard(workflow_id)
        return workflow_id

    async def start_knowledge_distill(self, workflow_id: str, payload: dict[str, Any]) -> str:
        """Records the start and, as no worker exists here, runs the same steps in this process."""
        from functools import partial

        from .knowledge.conversion import conversions
        from .knowledge.distill import distill

        if workflow_id not in self.started:
            self.started[workflow_id] = payload
            conversions.spawn(
                "distill",
                partial(
                    distill,
                    payload["org"],
                    payload["base_id"],
                    payload["requested_by"],
                    exclude=payload.get("exclude") or [],
                    max_usd=payload.get("max_usd"),
                ),
            )
        return workflow_id

    async def start_hosted_build(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.started.setdefault(workflow_id, payload)  # one build per key and attempt
        return workflow_id

    async def start_session_pod(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.start_calls.append(workflow_id)  # every call, for a test to count them
        self.started.setdefault(workflow_id, payload)  # one pod per session
        return workflow_id

    async def cancel(self, workflow_id: str) -> None:
        self.cancelled.append(workflow_id)


class RealTemporal:
    """Real Temporal client, connected lazily."""

    def __init__(self, address: str, namespace: str, task_queue: str) -> None:
        self.address = address
        self.namespace = namespace
        self.task_queue = task_queue
        self._client: Any = None
        self._lock = asyncio.Lock()

    async def client(self) -> Any:
        if self._client is None:
            async with self._lock:
                if self._client is None:
                    from temporalio.client import Client

                    self._client = await Client.connect(self.address, namespace=self.namespace)
        return self._client

    async def _start(self, workflow: str, workflow_id: str, payload: dict[str, Any]) -> str:
        from temporalio.exceptions import WorkflowAlreadyStartedError
        from temporalio.service import RPCError

        client = await self.client()
        try:
            handle = await client.start_workflow(
                workflow,
                payload,
                id=workflow_id,
                task_queue=self.task_queue,
                id_reuse_policy=2,  # ALLOW_DUPLICATE_FAILED_ONLY: no duplicate on one ticket
            )
            return str(handle.id)
        except WorkflowAlreadyStartedError:
            # Already running under this id: the expected case (one loop per source, one interpreter
            # per ticket). The SDK raises this, not an RPCError: before S39-29 a second "sync now"
            # on a knowledge source answered 500 on the dev tenant.
            logger.info("workflow already started", workflow_id=workflow_id)
            return workflow_id
        except RPCError as exc:  # already started: the intended behaviour
            if "already" in str(exc).lower():
                logger.info("workflow already started", workflow_id=workflow_id)
                return workflow_id
            raise

    async def start_interpreter(self, workflow_id: str, payload: dict[str, Any]) -> str:
        return await self._start("WorkflowInterpreter", workflow_id, payload)

    async def start_train(self, workflow_id: str, payload: dict[str, Any]) -> str:
        return await self._start("ReleaseTrain", workflow_id, payload)

    async def start_action(self, workflow_id: str, payload: dict[str, Any]) -> str:
        return await self._start("ActionWorkflow", workflow_id, payload)

    async def start_trial(self, workflow_id: str, payload: dict[str, Any]) -> str:
        """A draft agent version, tried once (S32-03): `AgentTrial` in the orchestrator."""
        return await self._start("AgentTrial", workflow_id, payload)

    async def start_provisioning(self, workflow_id: str, payload: dict[str, Any]) -> str:
        return await self._start("ProjectProvisioning", workflow_id, payload)

    async def start_knowledge_sync(self, workflow_id: str, payload: dict[str, Any]) -> str:
        return await self._start("KnowledgeSync", workflow_id, payload)

    async def start_reconciliation(self, workflow_id: str, payload: dict[str, Any]) -> str:
        """A project's tracker catch-up loop: `TrackerReconciliation` in the orchestrator."""
        return await self._start("TrackerReconciliation", workflow_id, payload)

    async def start_knowledge_distill(self, workflow_id: str, payload: dict[str, Any]) -> str:
        return await self._start("KnowledgeDistill", workflow_id, payload)

    async def start_memory_ingestion(self, workflow_id: str, payload: dict[str, Any]) -> str:
        """A project's memory loop (S51-02): `MemoryIngestion` in the orchestrator."""
        return await self._start("MemoryIngestion", workflow_id, payload)

    async def start_hosted_build(self, workflow_id: str, payload: dict[str, Any]) -> str:
        """A zip version's image (S41-08): `HostedImageBuild` in the orchestrator."""
        return await self._start("HostedImageBuild", workflow_id, payload)

    async def start_session_pod(self, workflow_id: str, payload: dict[str, Any]) -> str:
        """A hosted session's pod (S41-11): `AgentSessionPod` in the orchestrator."""
        return await self._start("AgentSessionPod", workflow_id, payload)

    async def signal(self, workflow_id: str, name: str, payload: Any) -> None:
        client = await self.client()
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(name, payload)

    async def query(self, workflow_id: str, name: str) -> Any:
        return await answer_or_none(workflow_id, name, self._query(workflow_id, name))

    async def _query(self, workflow_id: str, name: str) -> Any:
        client = await self.client()
        return await client.get_workflow_handle(workflow_id).query(name)

    async def cancel(self, workflow_id: str) -> None:
        client = await self.client()
        await client.get_workflow_handle(workflow_id).cancel()

    async def describe(self, workflow_id: str) -> WorkflowState | None:
        """The state of the workflow as Temporal sees it, or nothing if Temporal no longer knows it.

        Reading a ticket must not fail because Temporal is slow or absent: two seconds, then we
        answer "unknown", and the mark persisted on the ticket (`failure`) takes over.
        """
        from temporalio.service import RPCError

        try:
            client = await asyncio.wait_for(self.client(), timeout=2.0)
            description = await asyncio.wait_for(client.get_workflow_handle(workflow_id).describe(), 2.0)
        except (RPCError, TimeoutError, OSError):
            return None
        status = description.status.name if description.status is not None else "RUNNING"
        failure = None
        if status in {"FAILED", "TIMED_OUT", "TERMINATED"}:
            try:
                await client.get_workflow_handle(workflow_id).result()
            except Exception as exc:  # the message is what we want, not the exception
                failure = _readable_cause(exc)
        return WorkflowState(status=status, failure=failure)


async def answer_or_none(workflow_id: str, name: str, call: Awaitable[Any]) -> Any:
    """A workflow query's answer, or None when there is nothing to ask or no answer in time (S26-01).

    None covers: a workflow never started (a train is started lazily, on the first merge), one whose
    query fails, and one that does not answer within `QUERY_TIMEOUT_S`. The caller then answers from
    the database. Any other failure (Temporal unreachable, a bad request) still raises.
    """
    from temporalio.client import WorkflowQueryFailedError
    from temporalio.service import RPCError, RPCStatusCode

    try:
        return await asyncio.wait_for(call, QUERY_TIMEOUT_S)
    except TimeoutError:
        logger.warning("workflow query timed out", workflow_id=workflow_id, query=name)
        return None
    except WorkflowQueryFailedError as exc:
        logger.warning("workflow query failed", workflow_id=workflow_id, query=name, error=str(exc))
        return None
    except RPCError as exc:
        if exc.status not in {RPCStatusCode.NOT_FOUND, RPCStatusCode.DEADLINE_EXCEEDED}:
            raise
        logger.info("workflow query unanswered", workflow_id=workflow_id, query=name, error=str(exc))
        return None


def _readable_cause(exc: BaseException) -> str:
    """The message of the deepest cause.

    "the project has no repository", not "Activity task failed".
    """
    cause: BaseException = exc
    while cause.__cause__ is not None:
        cause = cause.__cause__
    message = getattr(cause, "message", None) or str(cause)
    return str(message)[:500]


_gateway: TemporalGateway | None = None


def get_temporal() -> TemporalGateway:
    global _gateway
    if _gateway is None:
        settings = get_settings()
        if settings.fakes:
            _gateway = FakeTemporal()
        else:
            _gateway = RealTemporal(
                settings.temporal_address, settings.temporal_namespace, settings.temporal_task_queue
            )
    return _gateway


def set_temporal(gateway: TemporalGateway | None) -> None:
    """Injection point for the tests."""
    global _gateway
    _gateway = gateway


def reconciliation_id(project_slug: str) -> str:
    """The id of a project's tracker catch-up loop, the one the orchestrator's worker starts."""
    return f"reconcile-{project_slug}"


async def start_tracker_reconciliation(project_slug: str) -> None:
    """Start the tracker catch-up loop of a project that just became active.

    The orchestrator's worker starts one loop per ACTIVE project when it starts: a project activated
    later was never polled until the next restart, so `candidates_jql` took nothing on (2026-11-02).
    Idempotent (deterministic id), and best effort: the activation stands if Temporal does not
    answer, and the next worker start catches the loop up.
    """
    interval = get_settings().reconcile_interval_seconds
    if interval <= 0:
        return
    try:
        await get_temporal().start_reconciliation(
            reconciliation_id(project_slug), {"project_slug": project_slug, "interval_seconds": interval}
        )
    except Exception as exc:  # the activation is saved; the worker start catches up
        logger.warning("tracker catch-up not started", project=project_slug, reason=str(exc))


async def stop_tracker_reconciliation(project_slug: str) -> None:
    """Stop an archived project's tracker catch-up loop. A loop that does not run is fine."""
    from temporalio.service import RPCError, RPCStatusCode

    try:
        await get_temporal().signal(reconciliation_id(project_slug), "stop", {})
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            logger.warning("tracker catch-up not stopped", project=project_slug, reason=str(exc))
    except Exception as exc:  # the archive is saved; a pass skips an archived project
        logger.warning("tracker catch-up not stopped", project=project_slug, reason=str(exc))


def memory_loop_id(project_slug: str) -> str:
    """The id of a project's memory loop (`MemoryIngestion`), the one the orchestrator's worker starts."""
    return f"mem-{project_slug}"


async def start_memory_ingestion(project_slug: str) -> None:
    """Start the memory loop of a project that just became active (S51-02).

    Nothing started `MemoryIngestion` before: the memory was never fed, and every signal to `mem-<slug>`
    went to a workflow that did not exist. Idempotent (deterministic id) and best effort, like the
    tracker catch-up: the activation stands if Temporal does not answer, the worker start catches up.
    The caller skips a project whose policy disables memory.
    """
    try:
        await get_temporal().start_memory_ingestion(
            memory_loop_id(project_slug), {"project_slug": project_slug}
        )
    except Exception as exc:  # the activation is saved; the worker start catches up
        logger.warning("memory loop not started", project=project_slug, reason=str(exc))


async def stop_memory_ingestion(project_slug: str) -> None:
    """Stop an archived project's memory loop. A loop that does not run is fine."""
    from temporalio.service import RPCError, RPCStatusCode

    try:
        await get_temporal().signal(memory_loop_id(project_slug), "stop", {})
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            logger.warning("memory loop not stopped", project=project_slug, reason=str(exc))
    except Exception as exc:  # the archive is saved; the loop only ingests
        logger.warning("memory loop not stopped", project=project_slug, reason=str(exc))


class MemoryLoopNotRunningError(Exception):
    """A project's memory loop does not run and could not be started: it was stopped (its project was
    archived) or Temporal refused the start. Nothing received the signal; the caller says so."""

    def __init__(self, workflow_id: str, cause: str) -> None:
        super().__init__(f"{workflow_id}: {cause}")
        self.workflow_id = workflow_id
        self.cause = cause


async def signal_memory_loop(project_slug: str, name: str, payload: Any) -> None:
    """Signal a project's memory loop, starting it first when it does not run (S51-02).

    A signal to a missing workflow is NOT_FOUND, which answered 500 on `reimport` and on the Alertmanager
    webhook. The loop is started (idempotent) and signalled once more; if it still cannot be reached,
    `MemoryLoopNotRunningError`. The caller checks the project's policy first.
    """
    from temporalio.service import RPCError, RPCStatusCode

    workflow_id = memory_loop_id(project_slug)
    try:
        await get_temporal().signal(workflow_id, name, payload)
        return
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise
    await get_temporal().start_memory_ingestion(workflow_id, {"project_slug": project_slug})
    try:
        await get_temporal().signal(workflow_id, name, payload)
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise
        raise MemoryLoopNotRunningError(workflow_id, str(exc)) from exc


class InterpreterNotRunningError(Exception):
    """A work item's interpreter does not run any more: Temporal answered NOT_FOUND to a signal (#679).

    It ended (completed, stopped, failed, terminated) or never started. Nothing received the signal;
    the caller says so instead of answering 500.
    """

    def __init__(self, workflow_id: str, cause: str) -> None:
        super().__init__(f"{workflow_id}: {cause}")
        self.workflow_id = workflow_id
        self.cause = cause


async def _signal_interpreter(project_slug: str, tracker_key: str, name: str, payload: Any) -> None:
    from temporalio.service import RPCError, RPCStatusCode

    workflow_id = interpreter_id(project_slug, tracker_key)
    try:
        await get_temporal().signal(workflow_id, name, payload)
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise
        raise InterpreterNotRunningError(workflow_id, str(exc)) from exc


async def deliver_decision(project_slug: str, tracker_key: str, decision: HumanDecision) -> None:
    await _signal_interpreter(project_slug, tracker_key, "human_decision", decision.model_dump(mode="json"))


async def deliver_inbound(project_slug: str, tracker_key: str, event: InboundEvent) -> None:
    await _signal_interpreter(project_slug, tracker_key, "inbound", event.model_dump(mode="json"))


async def deliver_control(project_slug: str, tracker_key: str, control: Control) -> None:
    await _signal_interpreter(project_slug, tracker_key, "control", control.model_dump(mode="json"))


_cause_lisible = _readable_cause  # lang: fr-ok alias:internal

alias_names(__name__, {"_cause_lisible": "_readable_cause"})  # lang: fr-ok alias:internal
