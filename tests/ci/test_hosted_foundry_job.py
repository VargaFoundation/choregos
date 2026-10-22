"""The CI job where an agent written for Foundry runs unchanged through the hosted suite (S41-19).

The conformance test skips when no agent is named, and a skipped test is a green job: these checks
keep the job naming the Foundry example, with its own pinned requirements, behind the gate.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
EXAMPLE = "examples/hosted/agent-framework-foundry"


def _workflow() -> dict[str, Any]:
    return dict(yaml.safe_load(CI.read_text(encoding="utf-8")))


def _job() -> dict[str, Any]:
    return dict(_workflow()["jobs"]["hosted-foundry"])


def _runs() -> str:
    return "\n".join(str(step.get("run", "")) for step in _job()["steps"])


def test_the_job_runs_the_foundry_example_with_its_requirements_and_fails_on_a_skip() -> None:
    runs = _runs()
    assert f"-r {EXAMPLE}/requirements.txt" in runs
    python = 'PYTHON="$RUNNER_TEMP/foundry/bin/python"'
    assert f"make conformance-hosted AGENT=agent-framework-foundry {python}" in runs
    assert "! grep -qi skipped pytest.out" in runs
    assert not any("if" in step for step in _job()["steps"]), "no step of the proof is skipped"


def test_the_gate_waits_for_the_job_and_the_filter_covers_what_it_proves() -> None:
    workflow = _workflow()
    assert "hosted-foundry" in workflow["jobs"]["ci-ok"]["needs"]
    assert "hosted-foundry" in workflow["jobs"]["changes"]["outputs"]
    filters = yaml.safe_load(workflow["jobs"]["changes"]["steps"][1]["with"]["filters"])["hosted-foundry"]
    for path in (
        f"{EXAMPLE}/**",
        "tests/conformance/hosted/**",
        "packages/runner/src/choregos_runner/loopback/**",
        "packages/core/src/choregos_core/hosted.py",
    ):
        assert path in filters, path
    assert "github.ref == 'refs/heads/main'" in _job()["if"]


def test_the_example_pins_every_requirement() -> None:
    lines = (ROOT / EXAMPLE / "requirements.txt").read_text(encoding="utf-8").split()
    assert lines and all("==" in line for line in lines), lines
