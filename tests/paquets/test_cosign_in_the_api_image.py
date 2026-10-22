"""The API image carries the `cosign` release the `hosted-cosign` job proves (S41-14, S41-14b).

Publication verifies a hosted image with the `cosign` copied into the API image; the CI job that runs
`CosignVerifier` against a real registry installs a release of its own. If the two drift, the job
proves a binary nobody ships. And v2.6.5, the last v2, links grpc 1.77, whose CVE-2026-33186
(CRITICAL, fixed in 1.79.3) the images job's Trivy gate refuses, and the release with it.
"""

from __future__ import annotations

import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker" / "api.Dockerfile"
CI = ROOT / ".github" / "workflows" / "ci.yml"

#: The first release checked to link a grpc without CVE-2026-33186 (grpc 1.82).
FLOOR = (3, 1, 3)


def _copied() -> tuple[str, str]:
    text = DOCKERFILE.read_text(encoding="utf-8")
    found = re.findall(
        r"^COPY\s+--from=ghcr\.io/sigstore/cosign/cosign:(v[\d.]+)@(sha256:[0-9a-f]{64})\s+\\\s*\n"
        r"\s*/ko-app/cosign\s+/usr/local/bin/cosign\s*$",
        text,
        re.M,
    )
    assert len(found) == 1, "the api Dockerfile copies cosign once, by tag and digest"
    return found[0]


def test_the_api_image_copies_the_release_the_hosted_cosign_job_runs() -> None:
    tag, _ = _copied()
    steps = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["hosted-cosign"]["steps"]
    installers = [step for step in steps if "sigstore/cosign-installer" in step.get("uses", "")]
    installed = [step["with"]["cosign-release"] for step in installers]
    assert installed == [tag], "the job proves the cosign the API image ships"


def test_the_cosign_release_links_a_grpc_without_cve_2026_33186() -> None:
    tag, _ = _copied()
    assert tuple(int(part) for part in tag.removeprefix("v").split(".")) >= FLOOR
