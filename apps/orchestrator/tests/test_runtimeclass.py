"""La RuntimeClass d'un pod d'agent : la politique d'abord, le déploiement ensuite."""

from __future__ import annotations

from types import SimpleNamespace

from choregos_core import load_preset
from choregos_orchestrator.activities.stage import classe_d_execution


def test_la_runtimeclass_vient_de_la_politique_sinon_du_deploiement() -> None:
    """Une politique `gvisor` ne se contourne pas par la valeur du chart ; sans exigence,
    le déploiement choisit (Kata, gVisor…), et sans rien, la runtime du cluster."""
    gvisor = load_preset("regulated")
    libre = load_preset("solo")
    assert gvisor.sandbox.runtime == "gvisor" and libre.sandbox.runtime != "gvisor"
    assert classe_d_execution(SimpleNamespace(runner_runtime_class="kata"), gvisor) == "gvisor"
    assert classe_d_execution(SimpleNamespace(runner_runtime_class="kata"), libre) == "kata"
    assert classe_d_execution(SimpleNamespace(runner_runtime_class=""), libre) is None
