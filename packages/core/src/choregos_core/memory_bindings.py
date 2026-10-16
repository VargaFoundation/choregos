# SPDX-License-Identifier: Apache-2.0
"""An agent version binds several memories (S40-11, ADR 0075): the pure rules.

`AgentSpec.memories` is a list of `Binding`: a memory (`global` to the organisation, or `project`,
relative to whichever project the run is in), read or read-and-write. Order is precedence. At most
one binding writes. A version that predates the field says `memory: none | project | agent`, which
`effective_bindings` turns into the equivalent list, so nothing stored is rewritten.

Everything here is pure: the API validates with `binding_problems`, the orchestrator resolves and
merges with the rest.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from choregos_contracts import ContextPack, MemoryItem, RelatedItem

from .memory_scope import global_store_namespace, project_store_namespace
from .models import estimate_tokens

MAX_BINDINGS = 8
#: The implicit project memories: the project's own (`shared`) and the agent's within it (`own`).
SHARED = "shared"
OWN = "own"
RESERVED_SLUGS = frozenset({SHARED, OWN})

BindingScope = Literal["global", "project"]
BindingMode = Literal["read", "write"]


@dataclass(frozen=True)
class Binding:
    scope: BindingScope
    slug: str
    mode: BindingMode = "read"

    @property
    def label(self) -> str:
        return f"{self.scope}:{self.slug}"

    @property
    def writes(self) -> bool:
        return self.mode == "write"


def effective_bindings(memories: Sequence[Binding] | None, memory: str = "project") -> list[Binding]:
    """The bindings of a version: `memories` when it is set, else derived from the legacy `memory`."""
    if memories is not None:
        return list(memories)
    if memory == "agent":
        return [Binding("project", OWN, "write")]
    if memory == "none":
        return []
    return [Binding("project", SHARED, "write")]


def legacy_scope(bindings: Sequence[Binding]) -> Literal["project", "agent", "none"] | None:
    """The legacy `memory` value that means exactly these bindings, if there is one."""
    if not bindings:
        return "none"
    if len(bindings) == 1 and bindings[0].mode == "write" and bindings[0].scope == "project":
        if bindings[0].slug == SHARED:
            return "project"
        if bindings[0].slug == OWN:
            return "agent"
    return None


def binding_problems(bindings: Sequence[Binding]) -> list[str]:
    """What is wrong with a list of bindings; empty when it is valid."""
    problems: list[str] = []
    if len(bindings) > MAX_BINDINGS:
        problems.append(f"at most {MAX_BINDINGS} memories may be bound, got {len(bindings)}")
    seen: set[str] = set()
    for binding in bindings:
        if binding.label in seen:
            problems.append(f"memory `{binding.label}` is bound twice")
        seen.add(binding.label)
        if binding.scope == "global" and binding.slug in RESERVED_SLUGS:
            problems.append(
                f"`{binding.slug}` is reserved for the project's own memories: it cannot be a global memory"
            )
    writers = [binding.label for binding in bindings if binding.writes]
    if len(writers) > 1:
        problems.append(f"at most one memory may be written to, got {len(writers)}: {', '.join(writers)}")
    return problems


def namespace_of_binding(org: str, project: str, agent: str | None, binding: Binding) -> str | None:
    """The namespace a binding names for a run of `project`; `None` for `own` without an agent."""
    if binding.scope == "global":
        return global_store_namespace(org, binding.slug)
    if binding.slug == SHARED:
        return project
    if binding.slug == OWN:
        return f"{project}#agent:{agent}" if agent else None
    return project_store_namespace(project, binding.slug)


def _score(item: MemoryItem) -> float:
    return item.score if item.score is not None else 0.0


def merge_context_packs(
    labelled_packs: Sequence[tuple[str, ContextPack]], budget_tokens: int, query: str = ""
) -> ContextPack:
    """One context pack out of the packs of several memories, in precedence order.

    Items are deduplicated by `subject` (the highest-precedence memory wins), then taken
    round-robin by precedence — best of memory 1, best of memory 2, ... then the second best of
    each — until the budget is spent. Scores of different stores are not comparable, and one large
    memory must not crowd the others out, hence no global score sort. Each item carries its
    memory's label.
    """
    seen: set[str] = set()
    queues: list[list[MemoryItem]] = []
    related: dict[str, RelatedItem] = {}
    paths: list[str] = []
    kinds: list[str] = []
    truncated = False
    for label, pack in labelled_packs:
        items = sorted([*pack.memories, *pack.incidents], key=lambda item: -_score(item))
        queue: list[MemoryItem] = []
        for item in items:
            if item.subject in seen:
                continue
            seen.add(item.subject)
            queue.append(item.model_copy(update={"memory": label}))
        queues.append(queue)
        truncated = truncated or pack.truncated
        for ref in pack.related_items:
            related.setdefault(ref.key, ref)
        paths.extend(path for path in pack.paths if path not in paths)
        kinds.extend(kind for kind in pack.kinds if kind not in kinds)
    taken: list[MemoryItem] = []
    tokens = 0
    exhausted = [False] * len(queues)
    position = 0
    while not all(exhausted[i] or position >= len(queues[i]) for i in range(len(queues))):
        for index, queue in enumerate(queues):
            if exhausted[index] or position >= len(queue):
                continue
            item = queue[position]
            cost = estimate_tokens(item.content)
            if tokens + cost > budget_tokens:
                truncated = True
                exhausted[index] = True
                continue
            tokens += cost
            taken.append(item)
        position += 1
    return ContextPack(
        query=query or next((pack.query for _, pack in labelled_packs if pack.query), ""),
        paths=paths,
        kinds=kinds,
        budget_tokens=budget_tokens,
        tokens_estimated=tokens,
        truncated=truncated,
        memories=[item for item in taken if item.kind != "incident"],
        incidents=[item for item in taken if item.kind == "incident"],
        related_items=list(related.values()),
        generated_at=next((pack.generated_at for _, pack in labelled_packs if pack.generated_at), None),
    )


__all__ = [
    "MAX_BINDINGS",
    "OWN",
    "RESERVED_SLUGS",
    "SHARED",
    "Binding",
    "binding_problems",
    "effective_bindings",
    "legacy_scope",
    "merge_context_packs",
    "namespace_of_binding",
]
