"""opencode does not call home from a run (S40-25).

Measured on the dev tenant (2026-10-23): a run made 8 calls to `models.opencode.ai:443`, each refused
by the per-run egress proxy (ADR 0070). The three flags below stop the catalogue fetch, the update
check and the language-server download.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from choregos_runner.backends import get_backend

from .test_units import _minimal_stage_input

FLAGS = (
    "OPENCODE_DISABLE_MODELS_FETCH",
    "OPENCODE_DISABLE_AUTOUPDATE",
    "OPENCODE_DISABLE_LSP_DOWNLOAD",
    "OPENCODE_DISABLE_DEFAULT_PLUGINS",
)


def _stage_env(
    backend_name: str, tmp_path: Path, key: str | None, launch_env: dict[str, str]
) -> dict[str, str]:
    """The environment the runner builds: the plan's, then the backend's model variables."""
    stage_input = _minimal_stage_input(tmp_path)
    stage_input.agent.launch.env = launch_env
    backend = get_backend(backend_name)
    plan = backend.launch_plan(stage_input, tmp_path)
    return {**plan.env, **backend.model_env(stage_input.model, key)}


@pytest.mark.parametrize("key", [None, "sk-run-key"])
def test_opencode_gets_the_offline_flags_with_or_without_a_key(tmp_path: Path, key: str | None) -> None:
    env = _stage_env("opencode", tmp_path, key, {})
    for flag in FLAGS:
        assert env[flag] == "1"


def test_an_agents_own_launch_env_wins(tmp_path: Path) -> None:
    env = _stage_env("opencode", tmp_path, "sk-run-key", {"OPENCODE_DISABLE_MODELS_FETCH": "0", "X": "y"})
    assert env["OPENCODE_DISABLE_MODELS_FETCH"] == "0"
    assert env["OPENCODE_DISABLE_AUTOUPDATE"] == "1"
    assert env["X"] == "y"


@pytest.mark.parametrize("backend", ["codex", "gemini-cli", "copilot-cli"])
def test_other_backends_do_not_get_them(tmp_path: Path, backend: str) -> None:
    env = _stage_env(backend, tmp_path, "sk-run-key", {})
    assert not [flag for flag in FLAGS if flag in env]
    assert "XDG_CONFIG_HOME" not in env


def test_opencode_reads_its_global_config_from_the_directory_the_image_bakes(tmp_path: Path) -> None:
    """`$HOME` is the mounted workspace volume, so the baked `node_modules` cannot live under it."""
    env = _stage_env("opencode", tmp_path, "sk-run-key", {})
    assert env["XDG_CONFIG_HOME"] == "/opt/choregos/xdg-config"


def test_the_workspace_opencode_directory_already_looks_installed(tmp_path: Path) -> None:
    """opencode skips its npm install when `node_modules` exists and the lock lists the plugin."""
    plan = get_backend("opencode").launch_plan(_minimal_stage_input(tmp_path), tmp_path)
    plan.materialize(tmp_path)
    assert (tmp_path / ".opencode/node_modules").is_dir()
    assert json.loads((tmp_path / ".opencode/package.json").read_text()) == {}
    lock = json.loads((tmp_path / ".opencode/package-lock.json").read_text())
    assert "@opencode-ai/plugin" in lock["packages"][""]["dependencies"]


def test_a_projects_own_opencode_package_json_is_left_alone(tmp_path: Path) -> None:
    (tmp_path / ".opencode").mkdir()
    (tmp_path / ".opencode/package.json").write_text('{"dependencies": {"x": "1"}}')
    plan = get_backend("opencode").launch_plan(_minimal_stage_input(tmp_path), tmp_path)
    plan.materialize(tmp_path)
    assert json.loads((tmp_path / ".opencode/package.json").read_text()) == {"dependencies": {"x": "1"}}


def test_the_provider_stays_the_bundled_openai_compatible_package(tmp_path: Path) -> None:
    """Bundled in the binary (measured on the pinned release): no install, so no pin to carry."""
    plan = get_backend("opencode").launch_plan(_minimal_stage_input(tmp_path), tmp_path)
    config = json.loads(plan.files["opencode.json"])
    assert config["provider"]["choregos"]["npm"] == "@ai-sdk/openai-compatible"
