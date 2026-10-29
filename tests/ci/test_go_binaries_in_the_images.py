"""The Go binaries of the published images link a Go stdlib without CVE-2026-56857.

CVE-2026-56857 (CRITICAL, fixed in Go 1.26.9 and 1.27.2) stopped the 0.21.0 release at the images
job's Trivy gate, in binaries copied from upstream releases, each measured with `go version`:
`cosign` v3.1.3 and `hubble` v1.19.4 in the API image (Go 1.26.4), `buildctl` v0.33.1 in the worker
(Go 1.26.8), `trivy` 0.74.0 and `gitleaks` v8.30.1 in the runner (Go 1.26.6, 1.24.13). `cosign`
v3.1.4 (Go 1.27.3), `buildctl` v0.34.1 (Go 1.26.10) and `trivy` 0.76.1 (Go 1.27.3) carry the fix.
No published `hubble` (v1.20.2: Go 1.27.1) nor `gitleaks` (v8.30.1 is the latest) does, so the
images build them from their tags with a fixed Go.

Building the image is the CI's job. These tests keep a later edit from going back to a release
binary, or to a Go that links the CVE, without anyone noticing.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker" / "api.Dockerfile"
RUNNER = ROOT / "docker" / "runner.Dockerfile"
STAGE = re.compile(r"^FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?\s*$", re.M | re.I)

#: The first Go of each line that carries the fix.
FIXED_GO = {(1, 26): 9, (1, 27): 2}


def _stages(dockerfile: pathlib.Path = DOCKERFILE) -> dict[str, tuple[str, str]]:
    text = dockerfile.read_text(encoding="utf-8")
    starts = list(STAGE.finditer(text))
    ends = [start.start() for start in starts[1:]] + [len(text)]
    return {
        start.group(2) or f"#{i}": (start.group(1), text[start.end() : end])
        for i, (start, end) in enumerate(zip(starts, ends, strict=True))
    }


def _fixed(version: str) -> bool:
    major, minor, patch = (int(part) for part in version.split("."))
    floor = FIXED_GO.get((major, minor))
    return (major, minor) > max(FIXED_GO) or (floor is not None and patch >= floor)


def test_hubble_is_built_from_its_tag_with_a_fixed_go() -> None:
    stages = _stages()
    assert "hubble" in stages, "the api Dockerfile builds hubble in a stage of its own"
    image, body = stages["hubble"]
    found = re.fullmatch(r"golang:(\d+\.\d+\.\d+)-\S+@sha256:[0-9a-f]{64}", image)
    assert found, f"the hubble stage's Go image is pinned by version and digest: {image}"
    assert _fixed(found.group(1)), f"Go {found.group(1)} links CVE-2026-56857"
    assert re.search(r'test "\$\(git rev-parse HEAD\)" = "\$\{HUBBLE_COMMIT\}"', body), (
        "the tag is checked against its commit"
    )
    assert re.search(r"^ARG HUBBLE_COMMIT=[0-9a-f]{40}$", body, re.M)
    assert "GOTOOLCHAIN=local" in body, "the pinned Go builds it, not a toolchain fetched at build"
    assert "-mod=vendor" in body, "the build is offline, from the dependencies in the tag"


def test_gitleaks_is_built_from_its_tag_with_a_fixed_go() -> None:
    stages = _stages(RUNNER)
    assert "gitleaks" in stages, "the runner Dockerfile builds gitleaks in a stage of its own"
    image, body = stages["gitleaks"]
    found = re.fullmatch(r"golang:(\d+\.\d+\.\d+)-\S+@sha256:[0-9a-f]{64}", image)
    assert found, f"the gitleaks stage's Go image is pinned by version and digest: {image}"
    assert _fixed(found.group(1)), f"Go {found.group(1)} links CVE-2026-56857"
    assert re.search(r'test "\$\(git rev-parse HEAD\)" = "\$\{GITLEAKS_COMMIT\}"', body)
    assert re.search(r"^ARG GITLEAKS_COMMIT=[0-9a-f]{40}$", body, re.M)
    assert "GOTOOLCHAIN=local" in body and "-mod=readonly" in body
    text = RUNNER.read_text(encoding="utf-8")
    assert "COPY --from=gitleaks /out/gitleaks /usr/local/bin/gitleaks" in text
    assert "COPY --from=zricethezav/" not in text, "no release image of gitleaks is copied any more"


def test_trivy_and_buildctl_are_releases_built_with_a_fixed_go() -> None:
    (trivy,) = re.findall(r"aquasec/trivy:([\d.]+)@sha256:[0-9a-f]{64}", RUNNER.read_text(encoding="utf-8"))
    # 0.76.1 is built with Go 1.27.3, 0.74.0 was built with 1.26.6.
    assert tuple(int(part) for part in trivy.split(".")) >= (0, 76, 1)
    (buildkit,) = re.findall(
        r"moby/buildkit:v([\d.]+)-rootless@sha256:[0-9a-f]{64}",
        DOCKERFILE.read_text(encoding="utf-8"),
    )
    # v0.34.1 is built with Go 1.26.10, v0.33.1 was built with 1.26.8.
    assert tuple(int(part) for part in buildkit.split(".")) >= (0, 34, 1)


def test_no_stage_takes_hubble_as_a_release_binary() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    assert "releases/download" not in text or "hubble-linux" not in text
    assert "COPY --from=hubble /out/hubble /usr/local/bin/hubble" in text


def test_cosign_is_a_release_binary_built_with_a_fixed_go() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    (release,) = re.findall(r"^ARG COSIGN_VERSION=v([\d.]+)$", text, re.M)
    # v3.1.4's release binaries are built with Go 1.27.3, measured with `go version`; its image
    # `ghcr.io/sigstore/cosign/cosign:v3.1.4` is another build, with Go 1.27.1: never copied.
    assert tuple(int(part) for part in release.split(".")) >= (3, 1, 4)
    assert "COPY --from=ghcr.io/sigstore/cosign" not in text


def test_the_fixed_go_check_reads_versions_right() -> None:
    assert not _fixed("1.26.4")
    assert not _fixed("1.27.1")
    assert _fixed("1.26.9")
    assert _fixed("1.27.2")
    assert _fixed("1.28.0")
