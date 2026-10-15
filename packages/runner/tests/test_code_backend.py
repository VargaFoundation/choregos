# SPDX-License-Identifier: Apache-2.0
"""The `code` backend (ADR 0063): the argv is the version's, the model comes from the run's key only."""

from __future__ import annotations

import json
from pathlib import Path

import choregos_contracts
import pytest
from choregos_contracts import ApiFormat, StageInput
from choregos_runner.backends import get_backend, known_backends
from choregos_runner.backends.code import CodeBackend


def _stage_input() -> StageInput:
    example = choregos_contracts.schemas_dir() / "examples" / "stage-input.code-agent.example.json"
    return StageInput.model_validate(json.loads(example.read_text(encoding="utf-8")))


def test_the_launch_plan_is_the_versions_argv(tmp_path: Path) -> None:
    stage_input = _stage_input()
    plan = get_backend("code").launch_plan(stage_input, tmp_path)
    assert plan.command == ["python", "-m", "ticket_triager"]
    assert plan.env["CHOREGOS_MAX_TURNS"] == str(stage_input.budget.max_turns)
    assert [server["name"] for server in plan.mcp_servers] == list(stage_input.tools.mcp)


def test_a_code_agent_without_a_command_is_refused(tmp_path: Path) -> None:
    stage_input = _stage_input()
    stage_input.agent.launch.command = []
    with pytest.raises(ValueError, match="needs a command"):
        get_backend("code").launch_plan(stage_input, tmp_path)


def test_the_model_reaches_the_agent_through_the_run_key() -> None:
    stage_input = _stage_input()
    env = CodeBackend().model_env(stage_input.model, "run-key-value")
    assert env["OPENAI_API_KEY"] == "run-key-value"
    assert env["OPENAI_BASE_URL"] == stage_input.model.base_url
    assert env["OPENAI_MODEL"] == stage_input.model.litellm_model


def test_without_a_run_key_no_credential_is_written() -> None:
    env = CodeBackend().model_env(_stage_input().model, None)
    assert set(env) == {"OPENAI_MODEL"}, "an empty key would overwrite the pod's own credentials"


def test_an_anthropic_runtime_gets_the_anthropic_variables() -> None:
    model = _stage_input().model.model_copy(update={"api_format": ApiFormat.ANTHROPIC})
    env = CodeBackend().model_env(model, "run-key-value")
    assert env["ANTHROPIC_AUTH_TOKEN"] == "run-key-value"
    assert "OPENAI_API_KEY" not in env


def test_the_code_backend_is_not_in_the_rotation() -> None:
    assert "code" not in known_backends()
    assert get_backend("code").name == "code"
