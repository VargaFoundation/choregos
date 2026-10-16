"""Chart tests render the chart offline, as a plain `helm template` would.

The chart's default executor is `agent_sandbox` (ADR 0072), and it refuses to render when the
operator's CRD is not among the cluster's API versions. Argo CD and `helm install` hand those
versions over; an offline `helm template` does not. Every test of this directory therefore
renders as if the operator were installed, except a test marked `no_operator`, which renders
the way a cluster without it would.
"""

from __future__ import annotations

import subprocess
from typing import Any

import pytest

OPERATOR_API = "extensions.agents.x-k8s.io/v1beta1/SandboxTemplate"


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "no_operator: render the chart as on a cluster without the agent-sandbox operator"
    )


@pytest.fixture(autouse=True)
def _operator_installed(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("no_operator"):
        return
    real_run = subprocess.run

    def run(args: Any, *positional: Any, **keywords: Any) -> Any:
        if isinstance(args, list) and args[:2] == ["helm", "template"] and "--api-versions" not in args:
            args = [*args, "--api-versions", OPERATOR_API]
        return real_run(args, *positional, **keywords)

    monkeypatch.setattr(subprocess, "run", run)
