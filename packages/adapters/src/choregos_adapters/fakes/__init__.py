# SPDX-License-Identifier: Apache-2.0
"""In-memory `Fake*` implementations of every adapter (S0-06).

Enabled by `CHOREGOS_FAKES=1`. They are **scriptable**: each fake exposes staging methods
(`seed`, `set_diff`, `set_status`, `queue_result`, `fail_next_analysis`, `record_usage`…) so that
tests read well.
"""

from __future__ import annotations

from .agent_sandbox import FakeSandboxCluster
from .builder import FakeImageBuilder
from .cd import FakeCd
from .ci import FakeCi
from .executor import FakeExecutor
from .gateway import BudgetExceeded, BudgetExceededError, FakeGateway
from .github_app import FakeGitHubApp
from .hosted_agent import FakeHostedAgent
from .memory import FakeMemory
from .model_server import FakeModelServer
from .notify import FakeNotifier
from .oci import FakeImageVerifier, FakeRegistryClient
from .realtime import FakeRealtimeUpstream
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
    "FakeHostedAgent",
    "FakeImageBuilder",
    "FakeImageVerifier",
    "FakeMemory",
    "FakeModelServer",
    "FakeNotifier",
    "FakeObjectStore",
    "FakeRealtimeUpstream",
    "FakeRegistryClient",
    "FakeSandboxCluster",
    "FakeScm",
    "FakeTracker",
]
