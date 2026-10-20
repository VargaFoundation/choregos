"""The CI job where a real rootless BuildKit builds the reference zip (S41-13, ADR 0080 §9).

The builder's unit tests prove the argv it sends; this job is what proves the argv builds an image,
twice the same, that passes the hosted conformance suite. These checks keep it from quietly
becoming something else: a daemon with another profile than the chart's, another image than the one
deployed, a gate that does not wait for it, or a filter that misses the files it proves.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
VALUES = ROOT / "charts" / "choregos" / "values.yaml"


def _workflow() -> dict[str, Any]:
    return dict(yaml.safe_load(CI.read_text(encoding="utf-8")))


def _job() -> dict[str, Any]:
    return dict(_workflow()["jobs"]["hosted-build"])


def _runs() -> str:
    return "\n".join(str(step.get("run", "")) for step in _job()["steps"])


def test_the_job_builds_the_reference_zip_then_runs_the_image_through_conformance() -> None:
    runs = _runs()
    assert "pytest tests/build -m buildkit" in runs
    assert 'make conformance-hosted IMAGE="$(cat "$RUNNER_TEMP/built-image")"' in runs
    env = next(step["env"] for step in _job()["steps"] if "tests/build" in str(step.get("run", "")))
    assert {"CHOREGOS_TEST_BUILDKIT_ADDRESS", "CHOREGOS_TEST_BUILD_REPOSITORY"} <= set(env), (
        "without them the test skips, and a skipped test is a green job"
    )
    assert env["CHOREGOS_TEST_BUILT_IMAGE_FILE"] == "${{ runner.temp }}/built-image"
    conditions = {str(step.get("if")) for step in _job()["steps"] if "if" in step}
    assert conditions == {"failure()"}, "no step of the proof is skipped, only the logs wait for a failure"


def test_the_daemon_is_the_image_and_the_profile_the_chart_deploys() -> None:
    chart = yaml.safe_load(VALUES.read_text(encoding="utf-8"))["global"]["buildkitd"]["image"]
    assert _job()["env"]["BUILDKIT_IMAGE"] == chart
    runs = _runs()
    for flag in (
        "--user 1000:1000",
        "--cap-drop ALL --cap-add SETUID --cap-add SETGID",
        "--security-opt seccomp=unconfined --security-opt apparmor=unconfined",
        "--read-only",
        "--oci-worker-no-process-sandbox",
    ):
        assert flag in runs, flag
    assert "--privileged" not in runs, "a privileged daemon would prove nothing of the chart's"


def test_buildctl_is_the_daemon_s_version_and_its_archive_is_checked() -> None:
    env = _job()["env"]
    assert f":{env['BUILDKIT_VERSION']}-rootless@sha256:" in env["BUILDKIT_IMAGE"]
    assert re.fullmatch(r"[0-9a-f]{64}", env["BUILDKIT_SHA256"])
    assert "sha256sum -c -" in _runs()


def test_the_gate_waits_for_the_job_and_the_filter_covers_what_it_proves() -> None:
    workflow = _workflow()
    assert "hosted-build" in workflow["jobs"]["ci-ok"]["needs"]
    assert "hosted-build" in workflow["jobs"]["changes"]["outputs"]
    filters = yaml.safe_load(workflow["jobs"]["changes"]["steps"][1]["with"]["filters"])["hosted-build"]
    for path in (
        "packages/adapters/src/choregos_adapters/oci/**",
        "apps/api/src/choregos_api/services/hosted_sources.py",
        "examples/hosted/reference/**",
        "tests/build/**",
    ):
        assert path in filters, path
    assert "github.ref == 'refs/heads/main'" in _job()["if"]


def test_the_tests_the_job_runs_are_in_the_repository() -> None:
    """`build/` in `.gitignore` once hid `tests/build/`: the job ran a directory its checkout lacked."""
    for path in ("tests/build/__init__.py", "tests/build/test_buildkit.py"):
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", path], cwd=ROOT, capture_output=True, check=False
        )
        assert tracked.returncode == 0, f"{path} is not tracked by git"
