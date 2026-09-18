"""Fabrique d'adaptateurs : du `connector.type` à l'implémentation.

`CHOREGOS_FAKES=1` bascule toute la plateforme sur les fakes en mémoire — c'est le mode
utilisé par les tests, le mode démo du front et `make demo`.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from choregos_contracts import ConnectorKind

from .base import (
    CdAdapter,
    CiAdapter,
    Executor,
    GatewayAdapter,
    MemoryAdapter,
    Notifier,
    ScmAdapter,
    TrackerAdapter,
)

Factory = Callable[[dict[str, Any]], Any]

_REGISTRY: dict[tuple[str, str], Factory] = {}


def register(kind: ConnectorKind | str, type_name: str) -> Callable[[Factory], Factory]:
    """Enregistre une implémentation pour un couple (kind, type)."""

    def decorator(factory: Factory) -> Factory:
        _REGISTRY[(str(kind), type_name)] = factory
        return factory

    return decorator


def fakes_enabled() -> bool:
    return os.environ.get("CHOREGOS_FAKES", "") in {"1", "true", "yes"}


def available(kind: ConnectorKind | str) -> list[str]:
    return sorted(type_name for (k, type_name) in _REGISTRY if k == str(kind))


def build(kind: ConnectorKind | str, type_name: str, config: dict[str, Any] | None = None) -> Any:
    """Construit un adaptateur. En mode fakes, le type demandé est ignoré."""
    config = config or {}
    if fakes_enabled():
        type_name = "fake"
    factory = _REGISTRY.get((str(kind), type_name))
    if factory is None:
        known = ", ".join(available(kind)) or "aucun"
        raise KeyError(f"aucun adaptateur `{type_name}` pour `{kind}` (disponibles : {known})")
    return factory(config)


@dataclass(slots=True)
class AdapterSet:
    """L'ensemble des adaptateurs d'un projet, résolu une fois puis passé aux activités."""

    tracker: TrackerAdapter
    scm: ScmAdapter
    ci: CiAdapter
    cd: CdAdapter
    executor: Executor
    memory: MemoryAdapter
    gateway: GatewayAdapter
    notify: Notifier
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def fakes(cls) -> AdapterSet:
        """Jeu complet de fakes, partageant les mêmes instances (utile en test)."""
        from .fakes import (
            FakeCd,
            FakeCi,
            FakeExecutor,
            FakeGateway,
            FakeMemory,
            FakeNotifier,
            FakeScm,
            FakeTracker,
        )

        return cls(
            tracker=FakeTracker(),
            scm=FakeScm(),
            ci=FakeCi(),
            cd=FakeCd(),
            executor=FakeExecutor(),
            memory=FakeMemory(),
            gateway=FakeGateway(),
            notify=FakeNotifier(),
        )

    @classmethod
    def from_connectors(cls, connectors: dict[str, dict[str, Any]]) -> AdapterSet:
        """Construit depuis la configuration des connecteurs d'un projet."""
        if fakes_enabled():
            return cls.fakes()

        def get(kind: str, default_type: str) -> Any:
            spec = connectors.get(kind, {})
            return build(kind, spec.get("type", default_type), spec.get("config", {}))

        return cls(
            tracker=get("tracker", "github-issues"),
            scm=get("scm", "github"),
            ci=get("ci", "tekton"),
            cd=get("cd", "argocd"),
            executor=get("runtime", "tekton"),
            memory=get("memory", "ecphoria"),
            gateway=get("gateway", "litellm"),
            notify=get("notify", "slack"),
        )
