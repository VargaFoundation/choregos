"""The `images` job scans the image on pull requests too (S40-16e, finding #867).

Trivy used to run on main only, against the pushed registry tag, so a CRITICAL showed up after the
merge (certifi, #857). On a pull request nothing is pushed: the image is loaded into the runner's
Docker under the same tag and the same Trivy step scans it.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
MAIN = "github.ref == 'refs/heads/main'"


def _steps() -> list[dict[str, Any]]:
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    return list(workflow["jobs"]["images"]["steps"])


def _step(prefix: str) -> dict[str, Any]:
    return next(s for s in _steps() if str(s.get("uses", "")).startswith(prefix))


def test_the_trivy_step_is_not_restricted_to_main() -> None:
    trivy = _step("aquasecurity/trivy-action")
    assert "if" not in trivy, "a condition on the scan hides a CRITICAL until it is on main"
    assert trivy["with"]["exit-code"] == "1"
    assert trivy["with"]["severity"] == "CRITICAL"


def test_the_scanned_image_is_one_a_pull_request_run_has() -> None:
    build = _step("docker/build-push-action")["with"]
    trivy = _step("aquasecurity/trivy-action")["with"]
    assert trivy["image-ref"] == build["tags"], "the scan must read the tag the build produces"
    assert build["push"] == "${{ " + MAIN + " }}", "main keeps pushing, pull requests do not"
    assert build["load"] == "${{ github.ref != 'refs/heads/main' }}", (
        "off main the image is loaded locally, otherwise there is nothing to scan"
    )
    assert "arm64" not in build["platforms"].split("||")[-1], "load takes a single platform"


def test_a_lockfile_an_image_copies_runs_the_images_job() -> None:
    """certifi came in through `uv.lock` (#857): a lockfile-only PR must reach the scan."""
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    paths_step = next(s for s in workflow["jobs"]["changes"]["steps"] if "filters" in s.get("with", {}))
    docker = yaml.safe_load(paths_step["with"]["filters"])["docker"]
    copied = {
        word.rstrip("*")
        for dockerfile in (ROOT / "docker").glob("*.Dockerfile")
        for line in dockerfile.read_text(encoding="utf-8").splitlines()
        if line.startswith("COPY ")
        for word in line.split()[1:-1]
        if "lock" in word.rsplit("/", 1)[-1] and not word.startswith("--")
    }
    assert copied, "no lockfile found in the Dockerfiles: the parsing is wrong"
    missing = copied - set(docker)
    assert not missing, f"lockfiles copied into an image but not in the `docker` filter: {missing}"
