"""Does the runner image keep opencode from installing packages at run time? (S40-25b)

On the dev tenant every run goes out through a per-run egress proxy that refuses
`registry.npmjs.org`, and opencode asks it for `@opencode-ai/plugin` once per config directory.
The image bakes that package into the XDG config root the launch plan points opencode at. Building
the image is the CI's job; these tests keep a later edit from removing the bake, its pin, or the
link between the image path and the launch plan.
"""

from __future__ import annotations

import pathlib
import re

from choregos_runner.backends.others import OPENCODE_CONFIG_HOME

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = (ROOT / "docker/runner.Dockerfile").read_text(encoding="utf-8")
LOCK = (ROOT / "packages/runner/src/choregos_runner/backends/versions.lock").read_text(encoding="utf-8")


def _pins() -> dict[str, str]:
    lines = (line for line in LOCK.splitlines() if line and not line.startswith("#"))
    return dict(line.split("=", 1) for line in lines)


def test_versions_lock_pins_the_plugin_to_the_opencode_release() -> None:
    pins = _pins()
    assert re.fullmatch(r"\d+\.\d+\.\d+", pins["opencode-plugin"]), pins.get("opencode-plugin")
    assert pins["opencode-plugin"] == pins["opencode"]


def test_the_image_installs_the_plugin_at_the_pin_where_the_launch_plan_points_opencode() -> None:
    root = f"{OPENCODE_CONFIG_HOME}/opencode"
    assert f"mkdir -p {root}" in DOCKERFILE
    assert '"@opencode-ai/plugin@$(version opencode-plugin)"' in DOCKERFILE
    assert "--ignore-scripts" in DOCKERFILE


def test_what_is_baked_belongs_to_the_runner_user_and_is_checked() -> None:
    assert "test -f node_modules/@opencode-ai/plugin/package.json" in DOCKERFILE
    assert f"chown -R 1000:1000 {pathlib.PurePosixPath(OPENCODE_CONFIG_HOME).parent}" in DOCKERFILE


def test_the_bake_comes_after_the_versions_lock_is_in_the_image() -> None:
    lock_copy = "COPY packages/runner/src/choregos_runner/backends/versions.lock /etc/choregos"
    assert DOCKERFILE.index(lock_copy) < DOCKERFILE.index('"@opencode-ai/plugin@')
