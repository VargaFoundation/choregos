"""The RuntimeClass of an agent pod: the policy first, the deployment second."""

from __future__ import annotations

from types import SimpleNamespace

from choregos_core import load_preset
from choregos_orchestrator.activities.stage import runtime_class


def test_the_runtimeclass_comes_from_the_policy_otherwise_from_the_deployment() -> None:
    """A `gvisor` policy is not bypassed by the chart value; with no requirement,
    the deployment chooses (Kata, gVisor...), and with nothing, the cluster runtime."""
    gvisor = load_preset("regulated")
    free = load_preset("solo")
    assert gvisor.sandbox.runtime == "gvisor" and free.sandbox.runtime != "gvisor"
    assert runtime_class(SimpleNamespace(runner_runtime_class="kata"), gvisor) == "gvisor"
    assert runtime_class(SimpleNamespace(runner_runtime_class="kata"), free) == "kata"
    assert runtime_class(SimpleNamespace(runner_runtime_class=""), free) is None
