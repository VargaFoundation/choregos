# SPDX-License-Identifier: Apache-2.0
"""Turn and loop limits, enforced by the runner rather than asked of the agent (S27-07).

ACP does not report the model's turns; what the runner sees is each tool call the agent makes (a
`tool_call` update, or a permission request naming one). A tool call is a turn of work: past
`max_turns` of them, the session is cancelled with `reason=max_turns`. The same tool called with the
same arguments `loop_threshold` times in a row is a loop: cancelled with `reason=loop`. Both used to
be written in the prompt only (`workspace.py`), which an agent is free to ignore.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: The same call this many times in a row is a loop, unless the deployment says otherwise.
DEFAULT_LOOP_THRESHOLD = 5


def signature(tool_call: dict[str, Any]) -> str:
    """What makes two tool calls the same: their kind (or title) and their arguments."""
    arguments = tool_call.get("rawInput")
    try:
        shown = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        shown = repr(arguments)
    return f"{tool_call.get('kind') or tool_call.get('title') or 'tool'}:{shown}"


@dataclass
class TurnGuard:
    max_turns: int | None = None
    loop_threshold: int = DEFAULT_LOOP_THRESHOLD
    turns: int = 0
    tripped: str | None = None
    detail: str = ""
    _seen: set[str] = field(default_factory=set)
    _last: str | None = None
    _repeats: int = 0

    def observe(self, tool_call: dict[str, Any]) -> str | None:
        """Counts a tool call (once per id); returns the reason to stop when a limit is crossed."""
        if self.tripped is not None:
            return self.tripped
        call_id = str(tool_call.get("toolCallId") or "")
        if call_id and call_id in self._seen:
            return None
        if call_id:
            self._seen.add(call_id)
        self.turns += 1
        current = signature(tool_call)
        self._repeats = self._repeats + 1 if current == self._last else 1
        self._last = current
        if self.loop_threshold > 1 and self._repeats >= self.loop_threshold:
            self.tripped = "loop"
            self.detail = f"the same call {self._repeats} times in a row: {current[:300]}"
        elif self.max_turns is not None and self.turns > self.max_turns:
            self.tripped = "max_turns"
            self.detail = f"{self.turns} tool calls, more than the stage's max_turns of {self.max_turns}"
        return self.tripped
