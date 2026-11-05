# SPDX-License-Identifier: Apache-2.0
"""A binary fetched from a GitHub release at image build time survives a transient error.

Release v0.23.0's `api (arm64)` image failed on one 504 from github.com while downloading
cosign, before any of our code ran, and a tag's release run cannot always be re-run. Every
`curl` of a `releases/download` or `archive/refs/tags` URL in docker/ retries, errors included.
Release v0.23.3's `api (amd64)` then saw github.com answer 504 for the whole 25 s that five
retries five seconds apart cover, so the retries must span at least two minutes.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GITHUB_DOWNLOAD = re.compile(r"releases/download|archive/refs/tags")
MIN_RETRY_WINDOW_S = 120


def _download_commands(text: str) -> list[str]:
    """Each `curl ...` command up to its `;`, line continuations joined."""
    joined = text.replace("\\\n", " ")
    return [c for c in re.findall(r"curl [^;]*", joined) if GITHUB_DOWNLOAD.search(c)]


def test_the_detector_sees_a_download_without_retry() -> None:
    sample = 'RUN curl -fsSL -o x \\\n  "https://github.com/a/b/releases/download/v1/x"; \\\n  echo ok'
    assert len(_download_commands(sample)) == 1
    assert "--retry" not in _download_commands(sample)[0]


def test_every_github_release_download_retries() -> None:
    commands = [
        (path.name, command)
        for path in sorted((ROOT / "docker").glob("*.Dockerfile"))
        for command in _download_commands(path.read_text(encoding="utf-8"))
    ]
    assert commands, "no GitHub release download found: the detector is broken"
    missing = [name for name, c in commands if "--retry-all-errors" not in c]
    assert not missing, f"downloads without --retry-all-errors in {missing}"


def _retry_window_s(command: str) -> int:
    """Seconds the retries of one `curl` command cover: retries times the delay between them."""
    retries = re.search(r"--retry (\d+)", command)
    delay = re.search(r"--retry-delay (\d+)", command)
    return int(retries.group(1)) * int(delay.group(1)) if retries and delay else 0


def test_the_window_is_read_from_the_flags() -> None:
    assert _retry_window_s("curl --retry 5 --retry-all-errors --retry-delay 5 -o x") == 25
    assert _retry_window_s("curl --retry 5 -o x") == 0


def test_the_retries_outlast_a_github_outage() -> None:
    short = [
        (path.name, _retry_window_s(command))
        for path in sorted((ROOT / "docker").glob("*.Dockerfile"))
        for command in _download_commands(path.read_text(encoding="utf-8"))
        if _retry_window_s(command) < MIN_RETRY_WINDOW_S
    ]
    assert not short, f"retries shorter than {MIN_RETRY_WINDOW_S} s: {short}"
