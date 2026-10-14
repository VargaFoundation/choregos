# SPDX-License-Identifier: Apache-2.0
"""Whose memory a run reads and writes (S32-02).

An agent version says `memory: none | project | agent`:

- `project` (the default, and the behaviour before S32-02): the project's shared memory;
- `agent`: the agent's own memory **within the project** — what it learnt is not mixed with what
  the project knows, and another agent does not read it;
- `none`: no memory read, nothing written.

The memory adapters take a single string to say where; the agent's memory is that string with a
suffix, `<project>#agent:<slug>`, so every adapter keeps its signature and a project's namespace
is unchanged.
"""

from __future__ import annotations

from typing import Literal

MemoryScope = Literal["none", "project", "agent"]
SEPARATOR = "#agent:"


def memory_namespace(project: str, scope: str, agent: str | None) -> str | None:
    """Where a run's memory lives; `None` when it has none."""
    if scope == "none":
        return None
    if scope == "agent" and agent:
        return f"{project}{SEPARATOR}{agent}"
    return project


def split_namespace(namespace: str) -> tuple[str, str | None]:
    """`(project, agent)` of a namespace; `agent` is `None` for the project's own memory."""
    projet, _, agent = namespace.partition(SEPARATOR)
    return projet, agent or None


__all__ = ["SEPARATOR", "MemoryScope", "memory_namespace", "split_namespace"]
