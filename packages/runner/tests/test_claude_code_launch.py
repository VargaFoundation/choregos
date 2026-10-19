"""`claude-code` starts the adapter the image installs, under the name it installs (#436).

`@zed-industries/claude-code-acp` is deprecated; the image installs
`@agentclientprotocol/claude-agent-acp`, whose binary is `claude-agent-acp` — the
`versions.lock` key the backend already pinned under. A stage input that names the old binary
still starts: the image links `claude-code-acp` to the new one
(`tests/paquets/test_claude_agent_acp_in_the_runner_image.py`).
"""

from __future__ import annotations

from pathlib import Path

from choregos_contracts import AgentRef, LaunchSpec
from choregos_runner.backends import BACKEND_BINARIES, get_backend

from .test_units import _minimal_stage_input


def test_the_default_command_is_the_binary_the_image_installs(tmp_path: Path) -> None:
    stage_input = _minimal_stage_input(tmp_path)
    stage_input.agent = AgentRef(backend="claude-code")
    plan = get_backend("claude-code").launch_plan(stage_input, tmp_path)
    assert plan.command == ["claude-agent-acp"]
    # The pin and the binary are one name: the image's `--version` check calls what is pinned.
    assert BACKEND_BINARIES["claude-code"] == plan.command[0]


def test_a_stage_input_naming_the_old_binary_keeps_it(tmp_path: Path) -> None:
    stage_input = _minimal_stage_input(tmp_path)
    stage_input.agent = AgentRef(backend="claude-code", launch=LaunchSpec(command=["claude-code-acp"]))
    plan = get_backend("claude-code").launch_plan(stage_input, tmp_path)
    assert plan.command == ["claude-code-acp"]
