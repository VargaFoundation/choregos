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
from typing import Any, Literal

MemoryScope = Literal["none", "project", "agent"]
SEPARATOR = "#agent:"
STORE_SEPARATOR = "#memory:"
ORG_PREFIX = "org:"
#: An assistant's memory space (S63-09, ADR 0136):
#: `org:<key>#assistant:<installation>[#user:<id>[#subject:<type>:<key>]]`.
ASSISTANT_SEPARATOR = "#assistant:"
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


MEMORY_KEY_SETTING = "memory_key"


def memory_key(organisation: Any) -> str:
    """The key an organisation's global memories live under (S40-24).

    The slug the organisation had when its slug first changed (`settings["memory_key"]`, written once
    and never overwritten), else its current slug. The slug is in every URL and may be renamed; the
    memory tenant `org:<key>` and the namespaces `org:<key>#memory:<store>` must not move with it, or
    every global memory would become invisible.
    """
    key = (getattr(organisation, "settings", None) or {}).get(MEMORY_KEY_SETTING)
    return str(key) if key else str(organisation.slug)


def assistant_space_namespace(
    org: str, installation: str, user: str | None = None, subject: tuple[str, str] | None = None
) -> str:
    """Where an assistant's memory space lives. Only `split_namespace` and `parse_namespace` know the form:
    both refuse it, so no run adapter can read a person's space as a project's memory."""
    if subject is not None and user is None:
        raise ValueError("a subject space belongs to a user")
    namespace = f"{ORG_PREFIX}{org}{ASSISTANT_SEPARATOR}{installation}"
    if user is not None:
        namespace += f"#user:{user}"
    if subject is not None:
        namespace += f"#subject:{subject[0]}:{subject[1]}"
    return namespace


def global_store_namespace(org: str, store: str) -> str:
    return f"{ORG_PREFIX}{org}{STORE_SEPARATOR}{store}"


def parse_namespace(namespace: str) -> ParsedNamespace:
    """The structure of a namespace string; raises `ValueError` on one that is none of the four forms
    (an assistant's space is a fifth, never read by a run: it raises too)."""
    if ASSISTANT_SEPARATOR in namespace:
        raise ValueError(f"an assistant's memory space is not a run's memory: {namespace!r}")
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
    if namespace.startswith(ORG_PREFIX) or STORE_SEPARATOR in namespace or ASSISTANT_SEPARATOR in namespace:
        raise ValueError(f"not a project or agent namespace (use parse_namespace): {namespace!r}")
    project, _, agent = namespace.partition(SEPARATOR)
    return project, agent or None


__all__ = [
    "ASSISTANT_SEPARATOR",
    "MEMORY_KEY_SETTING",
    "ORG_PREFIX",
    "SEPARATOR",
    "STORE_SEPARATOR",
    "MemoryScope",
    "ParsedNamespace",
    "assistant_space_namespace",
    "global_store_namespace",
    "memory_key",
    "memory_namespace",
    "parse_namespace",
    "project_store_namespace",
    "split_namespace",
]
