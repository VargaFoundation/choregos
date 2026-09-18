"""Implémentations `Fake*` en mémoire de tous les adaptateurs (S0-06).

Activées par `CHOREGOS_FAKES=1`. Elles sont **scriptables** : chaque fake expose des
méthodes de mise en scène (`seed`, `set_diff`, `set_status`, `queue_result`,
`fail_next_analysis`, `record_usage`…) pour écrire des tests lisibles.
"""

from __future__ import annotations

from .cd import FakeCd
from .ci import FakeCi
from .executor import FakeExecutor
from .gateway import BudgetExceeded, BudgetExceededError, FakeGateway
from .memory import FakeMemory
from .notify import FakeNotifier
from .scm import FakeScm
from .tracker import FakeTracker

__all__ = [
    "BudgetExceeded",
    "BudgetExceededError",
    "FakeCd",
    "FakeCi",
    "FakeExecutor",
    "FakeGateway",
    "FakeMemory",
    "FakeNotifier",
    "FakeScm",
    "FakeTracker",
]
