"""Is an agent's `python3` the image's interpreter, not the runner's venv? (#713)

The runner image put `/app/.venv/bin` first on `PATH`. Every child of the runner inherited it: an
agent's `python -m pytest` ran the runner's own interpreter, where pytest is not installed, and
code in the workspace could import the runner's packages. A live eval failed on it (dev, 0.18.5).

Building the image is the CI's job, and the build itself checks `python3`; these tests keep a
later edit from putting the venv back on `PATH` without anyone noticing.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker/runner.Dockerfile"


def _runtime() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    return text[text.index("AS runtime") :]


def test_the_runner_venv_is_not_on_the_image_path() -> None:
    paths = re.findall(r'PATH="([^"]*)"', _runtime())
    assert paths, "the runtime stage sets no PATH"
    assert all("/app/.venv" not in path for path in paths), paths


def test_the_runner_starts_by_its_absolute_path() -> None:
    entrypoint = re.search(r"^ENTRYPOINT (\[.*\])$", _runtime(), re.M)
    assert entrypoint and '"/app/.venv/bin/choregos-runner"' in entrypoint.group(1)


def test_the_platform_commands_stay_callable_by_name_and_the_build_checks_python3() -> None:
    runtime = _runtime()
    assert "for command in" in runtime, "the platform's commands are no longer linked"
    linked = runtime.split("for command in", 1)[1].split(";", 1)[0].split()
    # A Tekton step runs `choregos-runner run`, a job `choregos-quant`: linked, not on PATH.
    assert {"choregos-runner", "choregos-quant", "choregos-tools"} <= set(linked)
    assert 'ln -s "/app/.venv/bin/$command" "/usr/local/bin/$command"' in runtime
    assert 'test "$(command -v python3)" = /usr/local/bin/python3' in runtime
    assert "sys.prefix == sys.base_prefix" in runtime
