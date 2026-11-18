# SPDX-License-Identifier: Apache-2.0
"""One ACP session as the agent's code sees it: every side effect passes through here.

The runner governs a coding agent by what the agent *tells* it: a `tool_call` for each action, a
`session/request_permission` before a write or a command, a stop reason at the end. A code agent
gets no less. `RunSession.guarded` is the one door: it announces the call, asks the runner's guard,
runs the action only if the guard allowed it, and reports how it ended. The runner counts the call
(`max_turns`, loop detection), journals it and may refuse it; the code cannot skip any of that
without bypassing the SDK, which the docs say not to do and the sandbox bounds anyway.
"""

from __future__ import annotations

import itertools
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, TypeVar

from acp import start_tool_call, update_agent_message_text, update_tool_call
from acp.schema import PermissionOption, ToolCallUpdate

from .errors import MaxTurnsExceeded, PermissionDenied

T = TypeVar("T")
Kind = Literal["read", "edit", "delete", "move", "search", "execute", "think", "fetch", "other"]

ALLOW = "allow"
REJECT = "reject"
_OPTIONS = [
    PermissionOption(option_id=ALLOW, name="Allow", kind="allow_once"),
    PermissionOption(option_id=REJECT, name="Reject", kind="reject_once"),
]


@dataclass
class RunSession:
    conn: Any  # acp.interfaces.Client: the runner's side of the connection
    session_id: str
    workspace: Path
    mcp_servers: list[Any] = field(default_factory=list)
    max_turns: int | None = None
    #: Model calls and tool calls so far: the runner counts the same ones from outside.
    turns: int = 0
    _ids: Any = field(default_factory=lambda: itertools.count(1))

    def next_call_id(self) -> str:
        return f"sdk-{next(self._ids)}"

    async def say(self, text: str) -> None:
        """A message the runner's journal keeps; it has no other effect."""
        await self.conn.session_update(session_id=self.session_id, update=update_agent_message_text(text))

    async def guarded(
        self,
        title: str,
        kind: Kind,
        raw_input: dict[str, Any],
        action: Callable[[], Awaitable[T]],
        *,
        ask: bool = True,
        count: bool = True,
    ) -> T:
        """Announces a call, asks the guard (when `ask`), runs `action`, reports the outcome.

        Raises `MaxTurnsExceeded` before the call that would pass `max_turns` and `PermissionDenied`
        when the guard refuses; in both cases `action` does not run.
        """
        if count:
            if self.max_turns is not None and self.turns >= self.max_turns:
                raise MaxTurnsExceeded(
                    f"{self.turns} calls already made, the step's max_turns is {self.max_turns}"
                )
            self.turns += 1
        call_id = self.next_call_id()
        await self.conn.session_update(
            session_id=self.session_id,
            update=start_tool_call(call_id, title, kind=kind, status="pending", raw_input=raw_input),
        )
        if ask:
            allowed, reason = await self._ask(call_id, title, kind, raw_input)
            if not allowed:
                await self._report(call_id, "failed", {"refused": reason})
                raise PermissionDenied(title, reason)
        await self._report(call_id, "in_progress", None, raw_input=raw_input)
        try:
            result = await action()
        except BaseException as exc:
            await self._report(call_id, "failed", {"error": f"{type(exc).__name__}: {exc}"[:500]})
            raise
        await self._report(call_id, "completed", None)
        return result

    async def _ask(self, call_id: str, title: str, kind: Kind, raw_input: dict[str, Any]) -> tuple[bool, str]:
        response = await self.conn.request_permission(
            session_id=self.session_id,
            tool_call=ToolCallUpdate(
                tool_call_id=call_id, title=title, kind=kind, status="pending", raw_input=raw_input
            ),
            options=_OPTIONS,
        )
        outcome = response.outcome
        if getattr(outcome, "outcome", "") == "selected":
            chosen = next((o for o in _OPTIONS if o.option_id == outcome.option_id), None)
            if chosen is not None and chosen.kind.startswith("allow"):
                return True, ""
            return False, "refused by the runner's guard"
        return False, "the request was cancelled"

    async def _report(
        self, call_id: str, status: Literal["in_progress", "completed", "failed"], raw_output: Any, **kw: Any
    ) -> None:
        await self.conn.session_update(
            session_id=self.session_id,
            update=update_tool_call(call_id, status=status, raw_output=raw_output, **kw),
        )
