"""The CI jobs that build and run every hosted example with Docker (S41-23).

The real-Docker tests skip without `CHOREGOS_TEST_HOSTED_DOCKER=1`, and a skipped test is a green
job: these checks keep the jobs setting it, covering every example, failing on a skip and behind the gate.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
EXAMPLES = ROOT / "examples" / "hosted"


def _workflow() -> dict[str, Any]:
    return dict(yaml.safe_load(CI.read_text(encoding="utf-8")))


def _runs(job: dict[str, Any]) -> str:
    return "\n".join(str(step.get("run", "")) for step in job["steps"])


def test_the_matrix_names_every_example_directory() -> None:
    on_disk = sorted(
        p.name
        for p in EXAMPLES.iterdir()
        if p.is_dir() and ((p / "Dockerfile").exists() or (p / "choregos.json").exists())
    )
    matrix = _workflow()["jobs"]["hosted-images"]["strategy"]["matrix"]["example"]
    assert sorted(matrix) == on_disk, "a new example must be added to the matrix of `hosted-images`"


def test_both_jobs_opt_in_to_docker_and_fail_on_a_skip() -> None:
    jobs = _workflow()["jobs"]
    for name in ("hosted-images", "hosted-docker"):
        job = jobs[name]
        assert job["env"]["CHOREGOS_TEST_HOSTED_DOCKER"] == "1", name
        assert "! grep -qi skipped pytest.out" in _runs(job), name
        assert 'test "${PIPESTATUS[0]}" -eq 0' in _runs(job), "the pytest status is not hidden by tee"
        assert "github.ref == 'refs/heads/main'" in job["if"], name
    for name in ("hosted-images", "hosted-docker"):
        assert jobs[name]["env"]["CHOREGOS_TEST_HOSTED_REGISTRY"] == "localhost:5055", name
        assert "registry:2" in _runs(jobs[name]), name
    docker_run = _runs(jobs["hosted-docker"])
    assert "tests/conformance/hosted_real/test_shared_workspace.py" in docker_run
    assert "packages/runner/tests/test_serve_session_socket_docker.py" in docker_run


def test_the_gate_waits_for_both_jobs_and_the_filter_covers_what_they_prove() -> None:
    workflow = _workflow()
    for name in ("hosted-images", "hosted-docker"):
        assert name in workflow["jobs"]["ci-ok"]["needs"], name
    assert "hosted-images" in workflow["jobs"]["changes"]["outputs"]
    filters = yaml.safe_load(workflow["jobs"]["changes"]["steps"][1]["with"]["filters"])["hosted-images"]
    for path in (
        "examples/hosted/**",
        "tests/conformance/hosted_real/**",
        "tests/conformance/hosted/**",
        "packages/adapters/src/choregos_adapters/executor/local_docker.py",
        "packages/runner/src/choregos_runner/serve_session.py",
        "apps/api/src/choregos_api/services/hosted_sources.py",
    ):
        assert path in filters, path
