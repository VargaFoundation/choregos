"""What the real-Docker tests of hosted agents need (S41-23): a daemon, and for the builds a registry.

These tests build images and start containers, so they run only when asked for:
`CHOREGOS_TEST_HOSTED_DOCKER=1` (the `hosted-images` CI job and a laptop set it). The general
`python` job, which has a daemon too, must not start them.

`CHOREGOS_TEST_HOSTED_REGISTRY` names a registry the daemon can push to (`localhost:5055`, a
`registry:2` container); the `hosted-images` CI job and a laptop with Docker Desktop set it. Without
a daemon, or without the registry where one is needed, the tests skip, and the CI jobs fail on a skip.
"""

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys

import pytest

#: `tools/` is a folder of plain modules, not an installed package (as `tests/tools/conftest.py`).
ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REGISTRY = os.environ.get("CHOREGOS_TEST_HOSTED_REGISTRY", "")


def docker_available() -> bool:
    if os.environ.get("CHOREGOS_TEST_HOSTED_DOCKER") != "1":
        return False
    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"], capture_output=True, check=False
    )
    return probe.returncode == 0


@pytest.fixture(scope="session")
def docker() -> str:
    if not docker_available():
        pytest.skip("CHOREGOS_TEST_HOSTED_DOCKER=1 is not set, or no reachable Docker daemon")
    return "docker"


@pytest.fixture(scope="session")
def registry(docker: str) -> str:
    if not REGISTRY:
        pytest.skip("CHOREGOS_TEST_HOSTED_REGISTRY is not set: no registry to push a built image to")
    return REGISTRY
