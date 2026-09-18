"""Adaptateurs Choregos : tout ce qui parle au monde extérieur.

Un adaptateur implémente un `Protocol` de `base.py` et **n'a aucune logique métier** :
la décision est dans `packages/core`, l'orchestration dans `apps/orchestrator`.
"""

from __future__ import annotations

from .base import (
    AgentBackend,
    CdAdapter,
    CiAdapter,
    Executor,
    GatewayAdapter,
    MemoryAdapter,
    Notifier,
    ScmAdapter,
    TrackerAdapter,
)
from .registry import AdapterSet, available, build, fakes_enabled, register

__version__ = "0.1.0"

__all__ = [
    "AdapterSet",
    "AgentBackend",
    "CdAdapter",
    "CiAdapter",
    "Executor",
    "GatewayAdapter",
    "MemoryAdapter",
    "Notifier",
    "ScmAdapter",
    "TrackerAdapter",
    "available",
    "build",
    "fakes_enabled",
    "register",
]


def _register_builtins() -> None:
    """Enregistre les implémentations livrées (import paresseux pour éviter les cycles)."""
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

    register("tracker", "fake")(lambda cfg: FakeTracker(cfg.get("project_slug", "demo")))
    register("scm", "fake")(lambda cfg: FakeScm())
    register("ci", "fake")(lambda cfg: FakeCi())
    register("cd", "fake")(lambda cfg: FakeCd())
    register("runtime", "fake")(lambda cfg: FakeExecutor())
    register("memory", "fake")(lambda cfg: FakeMemory())
    register("gateway", "fake")(lambda cfg: FakeGateway())
    register("notify", "fake")(lambda cfg: FakeNotifier())


_register_builtins()
