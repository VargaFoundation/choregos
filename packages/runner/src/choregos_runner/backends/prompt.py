# SPDX-License-Identifier: Apache-2.0
"""The `prompt` backend: a prompt agent, carried by the runner's own tool loop (ADR 0080).

It launches nothing: `runner.py` runs `choregos_runner.prompt` instead of an ACP agent. The class is
here so that the backend's name is known, and so that its skills go where a backend that reads none
puts them — under `.choregos/skills`, with their index in the prompt.
"""

from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from choregos_contracts import ModelRef, StageInput

from .base import Backend, LaunchPlan


class PromptBackend(Backend):
    name: ClassVar[str] = "prompt"
    skills_dir: ClassVar[str | None] = None
    capabilities: ClassVar[frozenset[str]] = frozenset({"mcp"})

    def launch_plan(self, stage_input: StageInput, workspace: Path) -> LaunchPlan:
        raise ValueError("a prompt agent launches no process: the runner carries it (stage_input.prompt)")

    def model_env(self, model: ModelRef, key: str | None) -> dict[str, str]:
        return {}
