# SPDX-License-Identifier: Apache-2.0
"""Client Temporal côté API : démarrage de workflows et routage des signaux.

L'API ne connaît de Temporal que quatre gestes : démarrer un `WorkflowInterpreter`,
lui envoyer un signal, l'interroger, et signaler un train. En mode fakes, un client
en mémoire enregistre les appels pour que les tests et la démo fonctionnent sans serveur.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import Any, Protocol

from choregos_contracts import Control, HumanDecision, InboundEvent

from .config import get_settings
from .logging import get_logger

logger = get_logger("choregos.temporal")


def interpreter_id(project_slug: str, tracker_key: str) -> str:
    """ID déterministe : un même ticket ne peut pas avoir deux workflows (S1-10)."""
    safe = tracker_key.replace("/", "_").replace("#", "-")
    return f"wi-{project_slug}-{safe}"


def train_id(project_slug: str, env: str) -> str:
    return f"train-{project_slug}-{env}"


def findings_id(project_slug: str) -> str:
    return f"findings-{project_slug}"


def action_id(action: str) -> str:
    """L'identifiant Temporal d'une action gouvernée : un seul workflow par action (ADR 0035)."""
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
    async def cancel(self, workflow_id: str) -> None: ...
    async def describe(self, workflow_id: str) -> WorkflowState | None: ...


@dataclass
class WorkflowState:
    """Ce que Temporal sait d'un workflow : son statut, et pourquoi s'il est mort."""

    status: str  # RUNNING, COMPLETED, FAILED, CANCELED, TERMINATED, TIMED_OUT, CONTINUED_AS_NEW
    failure: str | None = None

    @property
    def dead(self) -> bool:
        return self.status in {"FAILED", "TERMINATED", "TIMED_OUT"}


@dataclass
class FakeTemporal:
    """Client en mémoire : enregistre démarrages et signaux, sans serveur Temporal."""

    started: dict[str, dict[str, Any]] = field(default_factory=dict)
    signals: list[tuple[str, str, Any]] = field(default_factory=list)
    queries: dict[str, Any] = field(default_factory=dict)
    cancelled: list[str] = field(default_factory=list)
    described: dict[str, WorkflowState] = field(default_factory=dict)
    #: workflows that no longer run: a signal to one is NOT_FOUND, as the real server answers (#679)
    gone: set[str] = field(default_factory=set)

    async def start_interpreter(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.started.setdefault(workflow_id, payload)  # ID déterministe ⇒ démarrage idempotent
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
        self.started.setdefault(workflow_id, payload)  # `action-<id>` : un seul démarrage
        return workflow_id

    async def start_trial(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.started.setdefault(workflow_id, payload)  # `trial-<work item>`: a single start
        return workflow_id

    async def start_knowledge_sync(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.started.setdefault(workflow_id, payload)  # one loop per source
        return workflow_id

    async def cancel(self, workflow_id: str) -> None:
        self.cancelled.append(workflow_id)


class RealTemporal:
    """Client Temporal réel, connecté paresseusement."""

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
                id_reuse_policy=2,  # ALLOW_DUPLICATE_FAILED_ONLY : pas de doublon sur un même ticket
            )
            return str(handle.id)
        except WorkflowAlreadyStartedError:
            # Already running under this id: the expected case (one loop per source, one interpreter
            # per ticket). The SDK raises this, not an RPCError: before S39-29 a second "sync now"
            # on a knowledge source answered 500 on the dev tenant.
            logger.info("workflow already started", workflow_id=workflow_id)
            return workflow_id
        except RPCError as exc:  # déjà démarré : c'est le comportement voulu
            if "already" in str(exc).lower():
                logger.info("workflow déjà démarré", workflow_id=workflow_id)
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
        """L'état du workflow vu de Temporal, ou rien si Temporal ne le connaît pas (ou plus).

        Une lecture de ticket ne doit pas tomber parce que Temporal est lent ou absent :
        deux secondes, puis on répond « inconnu », et la marque persistée sur le ticket
        (`failure`) prend le relais.
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
            except Exception as exc:  # c'est le message qu'on veut, pas l'exception
                failure = _cause_lisible(exc)
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


def _cause_lisible(exc: BaseException) -> str:
    """Le message de la cause la plus profonde.

    « le projet n'a pas de dépôt », pas « Activity task failed ».
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
    """Point d'injection pour les tests."""
    global _gateway
    _gateway = gateway


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
    await get_temporal().signal(
        interpreter_id(project_slug, tracker_key), "inbound", event.model_dump(mode="json")
    )


async def deliver_control(project_slug: str, tracker_key: str, control: Control) -> None:
    await _signal_interpreter(project_slug, tracker_key, "control", control.model_dump(mode="json"))
