"""The CI job where a hosted agent's pod runs on a real Kubernetes (S41-24, ADR 0080).

Every test of `tests/cluster/` skips without a cluster, an image or the RuntimeClass, and a skipped
test is a green job: these checks keep the job building the images, running the tests on 1.34 and
on 1.28, failing on a skip, behind the gate.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict[str, Any]:
    return dict(yaml.safe_load(CI.read_text(encoding="utf-8")))


def _job() -> dict[str, Any]:
    return dict(_workflow()["jobs"]["hosted-cluster"])


def _runs() -> str:
    return "\n".join(str(step.get("run", "")) for step in _job()["steps"])


def test_the_job_runs_the_hosted_tests_on_two_versions_and_fails_on_a_skip() -> None:
    runs = _runs()
    for test in ("test_hosted_native_sidecar.py", "test_hosted_session_pod.py", "test_hosted_gvisor.py"):
        assert f"tests/cluster/{test}" in runs, test
    assert "kindest/node:v1.28" in runs
    assert "CHOREGOS_CLUSTER_CONTEXT=kind-old" in runs
    assert "! grep -qi skipped pytest.out" in runs
    assert "make cluster-gvisor" in runs
    assert not any("if" in step for step in _job()["steps"]), "no step of the proof is skipped"


def test_the_images_are_built_and_loaded_into_the_cluster() -> None:
    runs = _runs()
    assert "examples/hosted/reference" in runs
    assert "tests/cluster/assets/runner.Dockerfile" in runs
    assert "kind load docker-image" in runs
    env = next(step["env"] for step in _job()["steps"] if "env" in step)
    assert env["CHOREGOS_HOSTED_REFERENCE_IMAGE"] == "reference-agent:ci"
    assert env["CHOREGOS_HOSTED_RUNNER_IMAGE"] == "choregos-runner:ci"


def test_the_gate_waits_for_the_job_and_the_filter_covers_what_it_proves() -> None:
    workflow = _workflow()
    assert "hosted-cluster" in workflow["jobs"]["ci-ok"]["needs"]
    assert "hosted-cluster" in workflow["jobs"]["changes"]["outputs"]
    filters = yaml.safe_load(workflow["jobs"]["changes"]["steps"][1]["with"]["filters"])["hosted-cluster"]
    for path in (
        "tests/cluster/**",
        "packages/adapters/src/choregos_adapters/executor/k8s_job.py",
        "packages/runner/src/choregos_runner/serve_session.py",
        "examples/hosted/reference/**",
    ):
        assert path in filters, path
    assert "github.ref == 'refs/heads/main'" in _job()["if"]
