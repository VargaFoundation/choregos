"""S40-21 (ADR 0084): the embedded LiteLLM loads the platform's LLM guard, the very file the tests prove."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"
CHART_COPY = CHART / "charts" / "litellm" / "files" / "choregos_guardrail.py"
SOURCE = ROOT / "packages" / "core" / "src" / "choregos_core" / "llm_guardrail.py"


def test_the_chart_ships_the_guard_the_tests_prove_byte_for_byte() -> None:
    """`choregos_core.llm_guardrail` is tested; the chart's copy is what LiteLLM runs."""
    assert CHART_COPY.read_bytes() == SOURCE.read_bytes(), (
        f"copy {SOURCE.relative_to(ROOT)} to {CHART_COPY.relative_to(ROOT)}"
    )


def _litellm(*overrides: str) -> dict[str, dict[str, Any]]:
    command = ["helm", "template", "choregos", str(CHART), "--set", "global.gateway.embedded=true"]
    for override in overrides:
        command += ["--set", override]
    rendered = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    return {
        doc["kind"]: doc
        for doc in yaml.safe_load_all(rendered)
        if doc and doc.get("metadata", {}).get("name") == "choregos-litellm"
    }


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")
def test_litellm_runs_the_guard_before_every_provider_call() -> None:
    docs = _litellm()
    data = docs["ConfigMap"]["data"]
    assert data["choregos_guardrail.py"] == SOURCE.read_text(encoding="utf-8")
    (guard,) = yaml.safe_load(data["config.yaml"])["guardrails"]
    assert guard["litellm_params"] == {
        "guardrail": "choregos_guardrail.ChoregosGuardrail",
        "mode": "pre_call",
        "default_on": True,
    }
    # LiteLLM finds `choregos_guardrail` next to its config: both come from the same ConfigMap.
    pod = docs["Deployment"]["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    assert container["args"][:2] == ["--config", "/etc/litellm/config.yaml"]
    assert {"name": "config", "mountPath": "/etc/litellm"} in container["volumeMounts"]


@pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")
def test_a_new_config_rolls_the_gateway() -> None:
    """The checksum covered the models only: a new upstream left the pod on the old config."""

    def checksum(*overrides: str) -> str:
        annotations = _litellm(*overrides)["Deployment"]["spec"]["template"]["metadata"]["annotations"]
        return str(annotations["checksum/config"])

    assert checksum() != checksum("choregos-litellm.upstream.apiBase=https://gw.example")
