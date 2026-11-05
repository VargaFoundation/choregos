# SPDX-License-Identifier: Apache-2.0
"""A binary fetched from a GitHub release at image build time survives a transient error.

Release v0.23.0's `api (arm64)` image failed on one 504 from github.com while downloading
cosign, before any of our code ran, and a tag's release run cannot always be re-run. Every
`curl` of a `releases/download` or `archive/refs/tags` URL in docker/ retries, errors included.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GITHUB_DOWNLOAD = re.compile(r"releases/download|archive/refs/tags")


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
