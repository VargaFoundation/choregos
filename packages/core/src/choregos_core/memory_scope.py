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

S40-11 adds named memories (ADR 0075) with two more forms of the same string:

- `<project>#memory:<slug>`: a named memory of that project;
- `org:<org>#memory:<slug>`: a global memory of the organisation.

`split_namespace` only knows the two old forms and **raises** on the new ones, so no adapter can
read a named memory as the project's shared one.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MemoryScope = Literal["none", "project", "agent"]
SEPARATOR = "#agent:"
STORE_SEPARATOR = "#memory:"
ORG_PREFIX = "org:"
NamespaceKind = Literal["project", "agent", "project_store", "global_store"]


@dataclass(frozen=True)
class ParsedNamespace:
    """What a namespace string names."""

    kind: NamespaceKind
    #: The organisation slug, for a global memory only.
    org: str | None = None
    #: The project slug; `None` for a global memory.
    project: str | None = None
    #: The agent slug, for an agent's own memory.
    agent: str | None = None
    #: The named memory's slug, for a named project memory or a global one.
    store: str | None = None


def memory_namespace(project: str, scope: str, agent: str | None) -> str | None:
    """Where a run's memory lives; `None` when it has none."""
    if scope == "none":
        return None
    if scope == "agent" and agent:
        return f"{project}{SEPARATOR}{agent}"
    return project


def project_store_namespace(project: str, store: str) -> str:
    return f"{project}{STORE_SEPARATOR}{store}"


def global_store_namespace(org: str, store: str) -> str:
    return f"{ORG_PREFIX}{org}{STORE_SEPARATOR}{store}"


def parse_namespace(namespace: str) -> ParsedNamespace:
    """The structure of a namespace string; raises `ValueError` on one that is none of the four forms."""
    if namespace.startswith(ORG_PREFIX):
        org, sep, store = namespace[len(ORG_PREFIX) :].partition(STORE_SEPARATOR)
        if not sep or not org or not store or SEPARATOR in store or STORE_SEPARATOR in store:
            raise ValueError(f"malformed global memory namespace: {namespace!r}")
        return ParsedNamespace("global_store", org=org, store=store)
    if STORE_SEPARATOR in namespace:
        project, _, store = namespace.partition(STORE_SEPARATOR)
        if not project or not store or SEPARATOR in namespace or STORE_SEPARATOR in store:
            raise ValueError(f"malformed project memory namespace: {namespace!r}")
        return ParsedNamespace("project_store", project=project, store=store)
    project, sep, agent = namespace.partition(SEPARATOR)
    if not project:
        raise ValueError(f"malformed memory namespace: {namespace!r}")
    if sep:
        if not agent:
            raise ValueError(f"malformed agent memory namespace: {namespace!r}")
        return ParsedNamespace("agent", project=project, agent=agent)
    return ParsedNamespace("project", project=project)


def split_namespace(namespace: str) -> tuple[str, str | None]:
    """`(project, agent)` of a namespace; `agent` is `None` for the project's own memory.

    Only the two forms that existed before S40-11. A named or global memory raises: answering
    `(project, None)` would read it as the project's shared memory.
    """
    if namespace.startswith(ORG_PREFIX) or STORE_SEPARATOR in namespace:
        raise ValueError(f"not a project or agent namespace (use parse_namespace): {namespace!r}")
    projet, _, agent = namespace.partition(SEPARATOR)
    return projet, agent or None


__all__ = [
    "ORG_PREFIX",
    "SEPARATOR",
    "STORE_SEPARATOR",
    "MemoryScope",
    "ParsedNamespace",
    "global_store_namespace",
    "memory_namespace",
    "parse_namespace",
    "project_store_namespace",
    "split_namespace",
]
