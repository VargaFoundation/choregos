# SPDX-License-Identifier: Apache-2.0
"""A run's steps, read back from its ACP journal (S67-03, ADR 0154 §2-3, §5).

No Rich, no Textual, no network: events in, steps out, so `choregos activity --run` and the workspace's
expanded node (S67-09) read a journal the same way. A tool call is born from a `tool_call` and lives by its
`tool_call_update`s, which carry only what changes; all share a `toolCallId`. The agent's plan is the whole
list of its tasks every time it publishes one (ACP `plan`: the client replaces it), or, for opencode, the
`todos` its `todowrite` call carries. Only what the agent wrote is shown: nothing is guessed from its prose.

What this module does NOT read is a backend's private `_meta` (ADR 0154 §5): which tool call delegated to a
sub-agent is the platform's to say, server-side (S67-12). Every tool call is flat under the run here.

An ACP update kind it does not know is skipped and counted (`unknown`), never raised: a newer agent must not
break an older terminal. The journal's kinds that are not steps by design (messages, thoughts, command lists,
mode changes) are ignored without being counted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

#: A tool call's states, as ACP names them.
PENDING, IN_PROGRESS, COMPLETED, FAILED = "pending", "in_progress", "completed", "failed"
CALL_STATUSES = frozenset({PENDING, IN_PROGRESS, COMPLETED, FAILED})
FINISHED = frozenset({COMPLETED, FAILED})
TASK_STATUSES = frozenset({PENDING, IN_PROGRESS, COMPLETED})
PRIORITIES = frozenset({"high", "medium", "low"})

#: ACP `session/update` kinds that are read as steps or plan.
STEP_KINDS = frozenset({"tool_call", "tool_call_update"})
PLAN_KIND = "plan"
#: ACP `session/update` kinds that are not steps and are not "unknown": the schema defines them.
NOT_STEPS = frozenset(
    {
        "user_message_chunk",
        "agent_message_chunk",
        "agent_thought_chunk",
        "available_commands_update",
        "current_mode_update",
        "config_option_update",
        "session_info_update",
        "usage_update",
    }
)

_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")


def clean(text: str) -> str:
    """What an agent wrote, safe to print: a control character (an escape sequence) never reaches the
    terminal, and a line break becomes a space."""
    return " ".join(_CONTROL.sub(" ", text).split())


@dataclass
class Step:
    """One tool call: its title, its ACP kind, its state, when it started and when it first finished."""

    id: str
    title: str
    kind: str
    status: str
    started_at: str
    ended_at: str | None = None


@dataclass(frozen=True)
class Task:
    """One task of the agent's plan."""

    content: str
    status: str
    priority: str


@dataclass
class RunSteps:
    """The steps and the plan of a run so far; `apply` can be called again with later events (`after_seq`)."""

    steps: dict[str, Step] = field(default_factory=dict)
    plan: list[Task] | None = None
    #: How many updates were of a kind this reader does not know (skipped).
    unknown: int = 0
    #: The highest event number read: what the next `after_seq` is.
    last_seq: int = 0
    _seen: set[int] = field(default_factory=set)

    def apply(self, events: list[dict[str, Any]]) -> None:
        """Read journal events, in the order of their `seq`. An event already read counts once: the stored
        journal and a later page can overlap."""
        for event in sorted(events, key=lambda e: int(e.get("seq") or 0)):
            seq = int(event.get("seq") or 0)
            if seq in self._seen:
                continue
            self._seen.add(seq)
            self.last_seq = max(self.last_seq, seq)
            if event.get("type") == "session/update":
                self._update(_update_of(event.get("payload")), str(event.get("ts") or ""))

    @classmethod
    def of(cls, events: list[dict[str, Any]]) -> RunSteps:
        reduced = cls()
        reduced.apply(events)
        return reduced

    def _update(self, update: dict[str, Any], ts: str) -> None:
        kind = update.get("sessionUpdate")
        if kind == PLAN_KIND:
            entries = update.get("entries")
            if isinstance(entries, list):
                self.plan = _tasks(entries)
        elif kind in STEP_KINDS:
            self._call(update, ts)
        elif kind not in NOT_STEPS:
            self.unknown += 1

    def _call(self, update: dict[str, Any], ts: str) -> None:
        call_id = update.get("toolCallId")
        if not isinstance(call_id, str) or not call_id:
            self.unknown += 1
            return
        step = self.steps.get(call_id)
        if step is None:
            step = self.steps[call_id] = Step(call_id, call_id, "other", PENDING, ts)
        title = update.get("title")
        if isinstance(title, str) and clean(title):
            step.title = clean(title)
        if isinstance(update.get("kind"), str):
            step.kind = clean(update["kind"]) or step.kind
        status = update.get("status")
        if status in CALL_STATUSES:
            step.status = status
            # The end is the FIRST update that finishes it: a repeat does not lengthen the call.
            if status in FINISHED and step.ended_at is None:
                step.ended_at = ts
        # opencode publishes its plan as the `todos` of its `todowrite` call, not as an ACP `plan` update.
        raw_input = update.get("rawInput")
        todos = raw_input.get("todos") if isinstance(raw_input, dict) else None
        named = f"{update.get('title') or ''} {update.get('name') or ''}".lower()
        if isinstance(todos, list) and "todo" in named:
            self.plan = _tasks(todos)

    def finished(self) -> int:
        return sum(1 for step in self.steps.values() if step.status in FINISHED)


def _update_of(payload: Any) -> dict[str, Any]:
    """The update of a `session/update` event: the runner journals the ACP params (`{"update": {...}}`)."""
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("update", payload)
    return inner if isinstance(inner, dict) else {}


def _tasks(entries: list[Any]) -> list[Task]:
    """A plan's tasks in the agent's order; a duplicate keeps its first place, a cancelled one leaves the plan
    (it is never shown as done)."""
    seen: set[str] = set()
    tasks: list[Task] = []
    for raw in entries:
        if not isinstance(raw, dict) or not isinstance(raw.get("content"), str):
            continue
        content = clean(raw["content"])
        if not content or content in seen or raw.get("status") == "cancelled":
            continue
        seen.add(content)
        status, priority = raw.get("status"), raw.get("priority")
        tasks.append(
            Task(
                content,
                status if status in TASK_STATUSES else PENDING,
                priority if priority in PRIORITIES else "medium",
            )
        )
    return tasks
