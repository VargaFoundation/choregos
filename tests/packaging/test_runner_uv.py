"""The runner image checks a project that develops with uv (S59-04).

A Python project with a `uv.lock` and a `requires-python` above the image's 3.12 (an operator's
project: `>=3.13`) could not be checked: `uv` was absent and no 3.13 existed, so every check failed and
the agents were told to fix a build they could not run. uv is pinned, Python 3.13 is installed at build
time and found without a download, and a run never fetches an interpreter.
"""

from __future__ import annotations

import re
from pathlib import Path

DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "runner.Dockerfile"


def _runtime() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    return text[text.index("AS runtime") :]


def test_uv_is_pinned_and_python_3_13_is_installed_at_build_time() -> None:
    runtime = _runtime()
    assert re.search(
        r"^COPY --from=ghcr\.io/astral-sh/uv:\d+\.\d+\.\d+ /uv /uvx /usr/local/bin/$", runtime, re.M
    )
    assert "uv python install 3.13" in runtime
    assert "UV_PYTHON_DOWNLOADS=never uv python find 3.13" in runtime, (
        "found at build time, without a download"
    )


def test_a_run_never_downloads_an_interpreter_and_python3_stays_the_images() -> None:
    runtime = _runtime()
    install = runtime.index("uv python install 3.13")
    assert re.search(r"^ENV UV_PYTHON_DOWNLOADS=never$", runtime[install:], re.M), "set after the install"
    assert 'test "$(command -v python3)" = /usr/local/bin/python3' in runtime[install:]
