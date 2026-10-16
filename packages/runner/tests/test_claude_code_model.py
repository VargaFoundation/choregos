"""`claude-code` talks to the model the run was given, and to no other (S28-02 finding).

The ACP adapter (`@zed-industries/claude-code-acp` 0.16.2, `dist/acp-agent.js`,
`getAvailableModels`) calls `query.setModel()` with the first model of the SDK's own list — an
alias, `default` — unless `.claude/settings.json` names a `model`. Claude Code resolves that alias
to its built-in Sonnet: on the local install on 2026-10-12 the gateway was asked for
`claude-sonnet-4-5-20250929`, and the run's virtual key, minted for one model, was refused.

`ANTHROPIC_MODEL` alone does not survive `setModel`. The aliases do, through
`ANTHROPIC_DEFAULT_{SONNET,OPUS,HAIKU}_MODEL`: each one is pinned to the run's model, and the
settings file names it too.
"""

from __future__ import annotations

import json
from pathlib import Path

from choregos_contracts import ModelRef
from choregos_runner.backends import get_backend

from .test_units import _minimal_stage_input

MODEL = "anthropic/claude-sonnet-5-5"


def _model() -> ModelRef:
    return ModelRef(litellm_model=MODEL, base_url="http://litellm:4000", api_format="anthropic")


def test_every_alias_claude_code_may_pick_resolves_to_the_runs_model() -> None:
    env = get_backend("claude-code").model_env(_model(), "sk-run")
    for alias in (
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "ANTHROPIC_SMALL_FAST_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
    ):
        assert env[alias] == MODEL, alias


def test_the_settings_file_names_the_runs_model(tmp_path: Path) -> None:
    stage_input = _minimal_stage_input(tmp_path)
    stage_input.model = _model()
    plan = get_backend("claude-code").launch_plan(stage_input, tmp_path)
    assert json.loads(plan.files[".claude/settings.json"])["model"] == MODEL


def test_the_built_in_web_tools_are_denied_exactly_when_the_run_switches_them_off(tmp_path: Path) -> None:
    """ADR 0070: `WebSearch` runs at the model provider, outside every network layer of the platform:
    denying it in the settings is the only lever there is."""
    stage_input = _minimal_stage_input(tmp_path)
    stage_input.model = _model()
    backend = get_backend("claude-code")

    on = json.loads(backend.launch_plan(stage_input, tmp_path).files[".claude/settings.json"])
    assert "WebFetch" not in on["permissions"]["deny"] and "WebSearch" not in on["permissions"]["deny"]

    stage_input.permissions.builtin_web_tools = False
    stage_input.permissions.deny_commands = ["kubectl"]
    off = json.loads(backend.launch_plan(stage_input, tmp_path).files[".claude/settings.json"])
    assert off["permissions"]["deny"] == ["Bash(kubectl)", "WebFetch", "WebSearch"]
