# SPDX-License-Identifier: Apache-2.0
"""The artifact flow of a workflow: which step produces each document, which step reads it.

`to_process` describes one step; this describes what travels between steps. It is computed once,
here, from the YAML only (no database), so the workflow page, the map and the ticket journey cannot
disagree. Only DECLARED outputs and inputs count: an agent that writes a document its transition
does not declare is invisible here, as it is to the gates.
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import Workflow
from choregos_contracts.workflow import HumanActor

#: The documents the platform hands every agent stage, whatever the transition declares
#: (`activities/stage.py`, `catalogue_d_agents/_contexte.md`).
CONTEXT_DOCUMENTS: tuple[str, ...] = ("spec_markdown", "plan_markdown", "allowed_paths")

#: What the pull request body carries, in order: `(document name, section title)`. A `None` title is
#: the bare text that heads the body. `activities/scm.py` iterates this tuple, so the map's "in the
#: pull request" flag and the real body cannot drift apart.
PULL_REQUEST_SECTIONS: tuple[tuple[str, str | None], ...] = (
    ("spec_markdown", None),
    ("plan_markdown", "Plan"),
    ("review_markdown", "Agent review"),
    ("release_notes_markdown", "Release notes"),
)

#: The document a person's rejection writes, read by the step the item goes back to.
FEEDBACK_DOCUMENT = "human_feedback"


def artifact_flow(wf: Workflow) -> list[dict[str, Any]]:
    """One entry per document name a workflow produces or reads.

    `origin` is `step` (a transition outputs it), `feedback` (`human_feedback`, written by a
    person's rejection), `field` (a property of `metadata.inputs`) or `missing` (read, but nothing
    produces it). Sorted by the YAML index of the first producer, then of the first reader, then by
    name: two calls give the same list.
    """
    produced: dict[str, list[str]] = {}
    read: dict[str, list[str]] = {}
    types: dict[str, str] = {}
    first_producer: dict[str, int] = {}
    first_reader: dict[str, int] = {}
    rejecting: list[str] = []
    for index, t in enumerate(wf.transitions):
        for name in t.outputs:
            keys = produced.setdefault(name, [])
            if t.key not in keys:
                keys.append(t.key)
            first_producer.setdefault(name, index)
            if name in t.output_types:
                types.setdefault(name, t.output_types[name])
        for name in t.inputs:
            keys = read.setdefault(name, [])
            if t.key not in keys:
                keys.append(t.key)
            first_reader.setdefault(name, index)
        if t.on_reject and isinstance(wf.actors.get(t.by) if t.by else None, HumanActor):
            rejecting.append(t.key)
    fields = set((wf.metadata.inputs or {}).get("properties") or {})
    names = set(produced) | set(read)
    if rejecting:
        names.add(FEEDBACK_DOCUMENT)
    pull_request = {name for name, _ in PULL_REQUEST_SECTIONS}
    entries: list[dict[str, Any]] = []
    for name in names:
        if name in produced:
            origin, by = "step", produced[name]
        elif name == FEEDBACK_DOCUMENT and rejecting:
            origin, by = "feedback", rejecting
        elif name in fields:
            origin, by = "field", []
        else:
            origin, by = "missing", []
        entries.append(
            {
                "name": name,
                "type": types.get(name),
                "produced_by": by,
                "read_by": read.get(name, []),
                "origin": origin,
                "context": name in CONTEXT_DOCUMENTS,
                "in_pull_request": name in pull_request,
            }
        )
    horizon = len(wf.transitions)

    def rank(entry: dict[str, Any]) -> tuple[int, int, str]:
        name = entry["name"]
        return (first_producer.get(name, horizon), first_reader.get(name, horizon), name)

    return sorted(entries, key=rank)
