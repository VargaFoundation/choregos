# SPDX-License-Identifier: Apache-2.0
"""What the agent's function receives: the task, the model, the tools, the places it may write."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .llm import Llm
from .session import RunSession
from .tools import Tools

OUTPUTS_DIR = Path(".choregos") / "outputs"


class Context:
    """One prompt turn of one run. Built by the SDK, never by the agent."""

    def __init__(
        self, session: RunSession, prompt: str, turn: int, env: Mapping[str, str] | None = None
    ) -> None:
        self._session = session
        self._prompt = prompt
        self.turn = turn
        self.llm = Llm(session, env)
        self.tools = _ToolsAccessor(session)

    # ── the task ────────────────────────────────────────────────────────────

    @property
    def prompt(self) -> str:
        """What the runner sent this turn: the playbook, then the task. On a repeat turn (a repair,
        a retry of the definition of done), the correction it asks for."""
        return self._prompt

    @property
    def task(self) -> str:
        """The step's task (`.choregos/task.md`): the work item, the inputs, the expected outputs.
        When the file is absent, the prompt."""
        path = self.workspace / ".choregos" / "task.md"
        return path.read_text(encoding="utf-8") if path.is_file() else self._prompt

    @property
    def workspace(self) -> Path:
        return self._session.workspace

    @property
    def fields(self) -> dict[str, Any]:
        """The work item's fields (`.choregos/fields.json`), when the runner wrote them."""
        path = self.workspace / ".choregos" / "fields.json"
        if not path.is_file():
            return {}
        loaded = json.loads(path.read_text(encoding="utf-8"))
        return dict(loaded) if isinstance(loaded, dict) else {}

    @property
    def inputs(self) -> list[Path]:
        """The files the step's declared inputs were mounted as (`.choregos/inputs/`)."""
        folder = self.workspace / ".choregos" / "inputs"
        return sorted(p for p in folder.rglob("*") if p.is_file()) if folder.is_dir() else []

    @property
    def skills(self) -> Path:
        """Where the agent's skills were laid out (`.agents/skills/<slug>/SKILL.md`)."""
        return self.workspace / ".agents" / "skills"

    # ── the model, the limits ───────────────────────────────────────────────

    @property
    def model(self) -> str | None:
        return self.llm.model

    @property
    def max_turns(self) -> int | None:
        return self._session.max_turns

    @property
    def turns(self) -> int:
        """Model calls and tool calls made so far in this run."""
        return self._session.turns

    # ── effects ─────────────────────────────────────────────────────────────

    async def write_output(self, name: str, content: str | bytes) -> Path:
        """Writes a deliverable to `.choregos/outputs/<name>`, after the runner's guard allowed it.

        The runner stores it as an artifact, never in a commit. Raises `PermissionDenied` when
        refused. `name` is a file name, not a path out of the folder.
        """
        relative = (OUTPUTS_DIR / name).as_posix()
        if not name or Path(name).is_absolute() or ".." in Path(name).parts:
            raise ValueError(f"an output name stays inside .choregos/outputs/: {name!r}")
        target = self.workspace / relative

        async def write() -> Path:
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")
            return target

        return await self._session.guarded(
            f"write {relative}", "edit", {"path": relative}, write, count=False
        )

    async def say(self, text: str) -> None:
        """A progress message for the run's journal."""
        await self._session.say(text)

    async def aclose(self) -> None:
        await self.tools.aclose()


class _ToolsAccessor:
    """`await ctx.tools()` returns the run's tools; the connection is opened on first call and
    closed with the step."""

    def __init__(self, session: RunSession) -> None:
        self._session = session
        self._tools: Tools | None = None

    async def __call__(self) -> Tools:
        if self._tools is None:
            self._tools = Tools(self._session)
            await self._tools.specs()
        return self._tools

    async def aclose(self) -> None:
        if self._tools is not None:
            await self._tools.aclose()


def env_int(name: str, env: Mapping[str, str] | None = None) -> int | None:
    value = (os.environ if env is None else env).get(name)
    return int(value) if value and value.isdigit() else None
