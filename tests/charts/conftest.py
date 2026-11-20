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


@pytest.fixture(autouse=True)
def _chart_names_are_adopted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The charts still render `CHOREGOS_*` (ADR 0176 R4: they move with the chart step), and a real
    process adopts them as `TRANIBOR_*` when it starts. A test that sets what the chart wrote in the
    environment gets the same adoption."""
    set_env = monkeypatch.setenv

    def setenv(name: str, value: str, prepend: str | None = None) -> None:
        set_env(name, value, prepend)
        if name.startswith("CHOREGOS_"):  # compat-ok
            set_env("TRANIBOR_" + name.removeprefix("CHOREGOS_"), value)  # compat-ok

    monkeypatch.setenv = setenv  # type: ignore[method-assign]
