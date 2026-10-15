# SPDX-License-Identifier: Apache-2.0
"""Turn and loop limits, enforced by the runner rather than asked of the agent (S27-07).

ACP does not report the model's turns; what the runner sees is each tool call the agent makes (a
`tool_call` update, or a permission request naming one). A tool call is a turn of work: past
`max_turns` of them, the session is cancelled with `reason=max_turns`. The same tool called with the
same arguments `loop_threshold` times in a row is a loop: cancelled with `reason=loop`. Both used to
be written in the prompt only (`workspace.py`), which an agent is free to ignore.

A call's arguments are often unknown when it is announced: opencode sends its `tool_call` with an
empty `rawInput` and fills it on a later `tool_call_update` (S22-44: five reads of five different
files fingerprinted as `read:{}` and stopped a working verifier). So a call is counted as a turn when
first seen, but fingerprinted only once its arguments are known — keyed by its id, from whichever
notification first carries them. A call that ends with its arguments never known is not compared:
it breaks the streak rather than extending it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

#: The same call this many times in a row is a loop, unless the deployment says otherwise.
DEFAULT_LOOP_THRESHOLD = 5


#: The statuses after which a call's arguments will not be reported any more.
_FINISHED = frozenset({"completed", "failed"})


def tool_name(tool_call: dict[str, Any]) -> str:
    """The tool a call uses: its kind and its title. The kind alone is too coarse — opencode
    reports every MCP tool as `other` — and is kept with the title of the call's first notification
    (a later title may be a file path, an argument rather than a name)."""
    kind = str(tool_call.get("kind") or "")
    title = str(tool_call.get("title") or "")
    if kind and title and title != kind:
        return f"{kind}/{title}"
    return kind or title or "tool"


def known_arguments(tool_call: dict[str, Any]) -> Any | None:
    """The call's arguments when this notification reports them; `None` when they are unknown
    (absent, `null`, or empty: an empty `rawInput` is the placeholder of a call just announced)."""
    arguments = tool_call.get("rawInput")
    if arguments is None or arguments in ({}, [], ""):
        return None
    return arguments


def signature(name: str, arguments: Any) -> str:
    """What makes two tool calls the same: the tool and its (known) arguments."""
    try:
        shown = json.dumps(arguments, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        shown = repr(arguments)
    return f"{name}:{shown}"


@dataclass
class TurnGuard:
    max_turns: int | None = None
    loop_threshold: int = DEFAULT_LOOP_THRESHOLD
    turns: int = 0
    tripped: str | None = None
    detail: str = ""
    #: The tool of each call seen, by id, until its fingerprint is taken.
    _names: dict[str, str] = field(default_factory=dict)
    #: The calls already compared (or given up on): a later update of them changes nothing.
    _settled: set[str] = field(default_factory=set)
    _last: str | None = None
    _repeats: int = 0
    _anonymous: int = 0

    def observe(self, tool_call: dict[str, Any]) -> str | None:
        """Takes in a `tool_call`, a `tool_call_update` or a permission request's `toolCall`.

        A call counts as one turn, the first time its id is seen; it is compared with the previous
        call the first time its arguments are known. Returns the reason to stop when a limit is
        crossed."""
        if self.tripped is not None:
            return self.tripped
        call_id = str(tool_call.get("toolCallId") or "")
        if not call_id:
            # No id to tie its updates to: this notification is all there will be of the call.
            self._anonymous += 1
            call_id = f"\x00anonymous-{self._anonymous}"
        if call_id not in self._names and call_id not in self._settled:
            self._names[call_id] = tool_name(tool_call)
            self.turns += 1
            if self.max_turns is not None and self.turns > self.max_turns:
                self.tripped = "max_turns"
                self.detail = f"{self.turns} tool calls, more than the stage's max_turns of {self.max_turns}"
                return self.tripped
        if call_id in self._settled:
            return None
        arguments = known_arguments(tool_call)
        if arguments is not None:
            self._compare(call_id, signature(self._names.pop(call_id), arguments))
        elif tool_call.get("status") in _FINISHED or call_id.startswith("\x00"):
            # Ended without its arguments ever being told: unknown is not "the same".
            self._names.pop(call_id, None)
            self._settled.add(call_id)
            self._last, self._repeats = None, 0
        return self.tripped

    def _compare(self, call_id: str, current: str) -> None:
        self._settled.add(call_id)
        self._repeats = self._repeats + 1 if current == self._last else 1
        self._last = current
        if self.loop_threshold > 1 and self._repeats >= self.loop_threshold:
            self.tripped = "loop"
            self.detail = f"the same call {self._repeats} times in a row: {current[:300]}"
