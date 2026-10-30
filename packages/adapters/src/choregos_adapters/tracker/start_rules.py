# SPDX-License-Identifier: Apache-2.0
"""What a tracker's backup poll searches for (S51-03, ADR 0113): `agent-ready`, and the project's
start rules a search by labels can express."""

from __future__ import annotations

from choregos_contracts import ProjectConfig

AGENT_READY = "agent-ready"


def label_searches(project: ProjectConfig) -> list[list[str]]:
    """Each a set of labels an item must all carry; their union is what the poll looks for. A rule
    on a type or a state is left to the webhook: a search by labels cannot express it."""
    searches = [[AGENT_READY]]
    for rule in project.start_rules:
        if rule.labels_only:
            searches.extend(found for found in rule.label_sets() if found not in searches)
    return searches
