"""The CI job where a hosted agent runs inside gVisor (S41-17, ADR 0080).

The conformance test under gVisor and the Kyverno test of the project policy both skip without
their binaries, and a skipped test is a green job: these checks keep the job running them, on the
release `dev/scripts/gvisor.sh` pins and checks, behind the gate.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
SCRIPT = ROOT / "dev" / "scripts" / "gvisor.sh"


def _workflow() -> dict[str, Any]:
    return dict(yaml.safe_load(CI.read_text(encoding="utf-8")))


def _job() -> dict[str, Any]:
    return dict(_workflow()["jobs"]["hosted-gvisor"])


def _runs() -> str:
    return "\n".join(str(step.get("run", "")) for step in _job()["steps"])


def test_the_job_runs_both_tests_with_runsc_and_fails_on_a_skip() -> None:
    runs = _runs()
    assert "pytest tests/conformance/hosted/test_hosted_under_gvisor.py -m gvisor" in runs
    assert "pytest apps/orchestrator/tests/test_gvisor_policy.py" in runs
    assert "! grep -qi skipped pytest.out" in runs
    env = next(step["env"] for step in _job()["steps"] if "gvisor -rs" in str(step.get("run", "")))
    assert env["CHOREGOS_TEST_RUNSC"] == "${{ runner.temp }}/gvisor/runsc"
    assert not any("if" in step for step in _job()["steps"]), "no step of the proof is skipped"


def test_the_release_is_the_script_s_and_its_archive_is_checked() -> None:
    assert 'dev/scripts/gvisor.sh fetch "$RUNNER_TEMP/gvisor"' in _runs()
    script = SCRIPT.read_text(encoding="utf-8")
    assert re.search(r"^GVISOR_RELEASE=\d{8}\.\d+$", script, re.MULTILINE)
    assert re.search(r"^GVISOR_SHA512=[0-9a-f]{128}$", script, re.MULTILINE)
    assert "sha512sum -c -" in script
    assert "releases/release/${GVISOR_RELEASE}/" in script


def test_the_gate_waits_for_the_job_and_the_filter_covers_what_it_proves() -> None:
    workflow = _workflow()
    assert "hosted-gvisor" in workflow["jobs"]["ci-ok"]["needs"]
    assert "hosted-gvisor" in workflow["jobs"]["changes"]["outputs"]
    filters = yaml.safe_load(workflow["jobs"]["changes"]["steps"][1]["with"]["filters"])["hosted-gvisor"]
    for path in (
        "tests/conformance/hosted/**",
        "apps/orchestrator/src/choregos_orchestrator/gitops.py",
        "apps/orchestrator/tests/test_gvisor_policy.py",
        "dev/scripts/gvisor.sh",
    ):
        assert path in filters, path
    assert "github.ref == 'refs/heads/main'" in _job()["if"]
