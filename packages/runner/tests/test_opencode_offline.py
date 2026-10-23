"""opencode does not call home from a run (S40-25).

Measured on the dev tenant (2026-10-23): a run made 8 calls to `models.opencode.ai:443`, each refused
by the per-run egress proxy (ADR 0070). The three flags below stop the catalogue fetch, the update
check and the language-server download.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from choregos_runner.backends import get_backend

from .test_units import _minimal_stage_input

FLAGS = ("OPENCODE_DISABLE_MODELS_FETCH", "OPENCODE_DISABLE_AUTOUPDATE", "OPENCODE_DISABLE_LSP_DOWNLOAD")


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
def test_opencode_gets_the_three_offline_flags_with_or_without_a_key(tmp_path: Path, key: str | None) -> None:
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
