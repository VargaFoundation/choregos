# SPDX-License-Identifier: Apache-2.0
"""The `code` backend: an agent written in code, an ACP agent in an operator-named image (ADR 0063).

Unlike the other backends it ships no agent of its own: the command is the one the agent's version
declares (`agent.launch.command`), run in the image the operator mapped to the agent's image name.
It speaks ACP on stdio, so everything the runner guarantees for a coding agent applies unchanged —
permission requests, the turn guard, the journal, cancellation. The model reaches it only through
the environment below (the run's gateway key, never a provider key).
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from choregos_contracts import ApiFormat, ModelRef, StageInput

from .base import Backend, LaunchPlan


class CodeBackend(Backend):
    name: ClassVar[str] = "code"
    skills_dir: ClassVar[str | None] = ".agents/skills"
    capabilities: ClassVar[frozenset[str]] = frozenset({"acp", "mcp"})

    def launch_plan(self, stage_input: StageInput, workspace: Path) -> LaunchPlan:
        command = list(stage_input.agent.launch.command)
        if not command:
            raise ValueError("an agent written in code needs a command: its version declares none")
        return LaunchPlan(
            command=command,
            env={
                **stage_input.agent.launch.env,
                # The SDK stops the agent at this many model calls: the same limit the runner's
                # turn guard enforces from outside.
                "CHOREGOS_MAX_TURNS": str(stage_input.budget.max_turns),
            },
            files=dict(stage_input.agent.launch.files),
            mcp_servers=self.mcp_servers(stage_input),
        )

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        if model.api_format == ApiFormat.ANTHROPIC:
            return self.anthropic_env(model, key)
        return self.openai_env(model, key)
