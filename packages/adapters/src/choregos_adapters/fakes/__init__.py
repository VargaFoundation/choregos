# SPDX-License-Identifier: Apache-2.0
"""Implémentations `Fake*` en mémoire de tous les adaptateurs (S0-06).

Activées par `CHOREGOS_FAKES=1`. Elles sont **scriptables** : chaque fake expose des
méthodes de mise en scène (`seed`, `set_diff`, `set_status`, `queue_result`,
`fail_next_analysis`, `record_usage`…) pour écrire des tests lisibles.
"""

from __future__ import annotations

from .agent_sandbox import FakeSandboxCluster
from .cd import FakeCd
from .ci import FakeCi
from .executor import FakeExecutor
from .gateway import BudgetExceeded, BudgetExceededError, FakeGateway
from .github_app import FakeGitHubApp
from .memory import FakeMemory
from .notify import FakeNotifier
from .oci import FakeImageVerifier, FakeRegistryClient
from .scm import FakeScm
from .storage import FakeObjectStore
from .tracker import FakeTracker

__all__ = [
    "BudgetExceeded",
    "BudgetExceededError",
    "FakeCd",
    "FakeCi",
    "FakeExecutor",
    "FakeGateway",
    "FakeGitHubApp",
    "FakeImageVerifier",
    "FakeMemory",
    "FakeNotifier",
    "FakeObjectStore",
    "FakeRegistryClient",
    "FakeSandboxCluster",
    "FakeScm",
    "FakeTracker",
]
