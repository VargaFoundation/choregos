"""Envoi de signaux aux workflows Temporal depuis une activité (train, findings, mémoire).

En mode fakes, les signaux sont mis en file en mémoire : la démo et les tests fonctionnent
sans serveur Temporal, et le contenu des signaux reste vérifiable.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .config import get_settings

_FAKE_SIGNALS: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
_FAKE_STARTS: dict[str, dict[str, Any]] = {}


def fake_signals(workflow_id: str | None = None) -> Any:
    if workflow_id is None:
        return dict(_FAKE_SIGNALS)
    return list(_FAKE_SIGNALS.get(workflow_id, []))


def fake_starts() -> dict[str, dict[str, Any]]:
    return dict(_FAKE_STARTS)


def clear_fake_signals() -> None:
    _FAKE_SIGNALS.clear()
    _FAKE_STARTS.clear()


async def start_workflow_once(name: str, workflow_id: str, payload: dict[str, Any]) -> bool:
    """Démarre un workflow s'il n'existe pas déjà sous cet identifiant.

    Rend `True` si ce démarrage-ci a créé le workflow. Un identifiant déterministe rend
    le geste rejouable : ni le rattrapage périodique ni un webhook rejoué ne peuvent
    faire tourner deux fois le même ticket.
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
            # Déjà terminé pour ce ticket : on ne le relance pas. Déjà en cours : on reprend
            # la main sur l'existant plutôt que d'en créer un second.
            id_reuse_policy=WorkflowIDReusePolicy.REJECT_DUPLICATE,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        )
    except WorkflowAlreadyStartedError:
        return False
    return True


async def signal_workflow(
    workflow_id: str, name: str, payload: dict[str, Any], *, start: str | None = None
) -> None:
    """Signale un workflow ; le démarre si `start` donne son type et qu'il n'existe pas."""
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
