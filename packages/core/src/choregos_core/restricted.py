# SPDX-License-Identifier: Apache-2.0
"""A work item marked by the restricted knowledge bases its runs were served (ADR 0114).

Shared by the API and the orchestrator (ADR 0099): the mark itself, and what leaves the platform in
place of a marked work item's content. Who may read a marked work item is the API's business
(`choregos_api.knowledge.marked`).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

#: What leaves the platform in place of a marked work item's content (ADR 0114, point 6 and choice A).
WITHHELD = "withheld: this work item read a restricted knowledge base; read it in the console"
#: The title shown for a marked work item an agent named (a fan-out child, a finding's).
RESTRICTED_TITLE = "Restricted work item"


def is_marked(item: Any) -> bool:
    """Whether the work item's runs were served a restricted base."""
    return bool(item is not None and getattr(item, "restricted_knowledge", None))


def mark(item: Any, slugs: Iterable[str]) -> bool:
    """Adds these bases to the work item's mark, which is only ever added to; whether anything was."""
    before = set(item.restricted_knowledge or [])
    after = before | set(slugs)
    if after == before:
        return False
    item.restricted_knowledge = sorted(after)
    return True
