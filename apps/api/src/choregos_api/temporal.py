"""Client Temporal côté API : démarrage de workflows et routage des signaux.

L'API ne connaît de Temporal que quatre gestes : démarrer un `WorkflowInterpreter`,
lui envoyer un signal, l'interroger, et signaler un train. En mode fakes, un client
en mémoire enregistre les appels pour que les tests et la démo fonctionnent sans serveur.
"""

from __future__ import annotations

import asyncio
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


def provisioning_id(project_slug: str) -> str:
    return f"prov-{project_slug}"


class TemporalGateway(Protocol):
    async def start_interpreter(self, workflow_id: str, payload: dict[str, Any]) -> str: ...
    async def signal(self, workflow_id: str, name: str, payload: Any) -> None: ...
    async def query(self, workflow_id: str, name: str) -> Any: ...
    async def start_train(self, workflow_id: str, payload: dict[str, Any]) -> str: ...
    async def start_provisioning(self, workflow_id: str, payload: dict[str, Any]) -> str: ...
    async def cancel(self, workflow_id: str) -> None: ...


@dataclass
class FakeTemporal:
    """Client en mémoire : enregistre démarrages et signaux, sans serveur Temporal."""

    started: dict[str, dict[str, Any]] = field(default_factory=dict)
    signals: list[tuple[str, str, Any]] = field(default_factory=list)
    queries: dict[str, Any] = field(default_factory=dict)
    cancelled: list[str] = field(default_factory=list)

    async def start_interpreter(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.started.setdefault(workflow_id, payload)  # ID déterministe ⇒ démarrage idempotent
        return workflow_id

    async def signal(self, workflow_id: str, name: str, payload: Any) -> None:
        self.signals.append((workflow_id, name, payload))

    async def query(self, workflow_id: str, name: str) -> Any:
        return self.queries.get(f"{workflow_id}:{name}")

    async def start_train(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.started.setdefault(workflow_id, payload)
        return workflow_id

    async def start_provisioning(self, workflow_id: str, payload: dict[str, Any]) -> str:
        self.started[workflow_id] = payload
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
        except RPCError as exc:  # déjà démarré : c'est le comportement voulu
            if "already" in str(exc).lower():
                logger.info("workflow déjà démarré", workflow_id=workflow_id)
                return workflow_id
            raise

    async def start_interpreter(self, workflow_id: str, payload: dict[str, Any]) -> str:
        return await self._start("WorkflowInterpreter", workflow_id, payload)

    async def start_train(self, workflow_id: str, payload: dict[str, Any]) -> str:
        return await self._start("ReleaseTrain", workflow_id, payload)

    async def start_provisioning(self, workflow_id: str, payload: dict[str, Any]) -> str:
        return await self._start("ProjectProvisioning", workflow_id, payload)

    async def signal(self, workflow_id: str, name: str, payload: Any) -> None:
        client = await self.client()
        handle = client.get_workflow_handle(workflow_id)
        await handle.signal(name, payload)

    async def query(self, workflow_id: str, name: str) -> Any:
        client = await self.client()
        handle = client.get_workflow_handle(workflow_id)
        return await handle.query(name)

    async def cancel(self, workflow_id: str) -> None:
        client = await self.client()
        await client.get_workflow_handle(workflow_id).cancel()


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


async def deliver_decision(project_slug: str, tracker_key: str, decision: HumanDecision) -> None:
    await get_temporal().signal(
        interpreter_id(project_slug, tracker_key), "human_decision", decision.model_dump(mode="json")
    )


async def deliver_inbound(project_slug: str, tracker_key: str, event: InboundEvent) -> None:
    await get_temporal().signal(
        interpreter_id(project_slug, tracker_key), "inbound", event.model_dump(mode="json")
    )


async def deliver_control(project_slug: str, tracker_key: str, control: Control) -> None:
    await get_temporal().signal(
        interpreter_id(project_slug, tracker_key), "control", control.model_dump(mode="json")
    )
