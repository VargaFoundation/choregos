# SPDX-License-Identifier: Apache-2.0
"""The commit a work item's gates judged (ADR 0171): read once by the evaluation, named by the merge.

A gate evaluation reads the head of the work item's branch once and rules on that commit. It leaves
the commit on the work item, with the state the work item was in, and the merge names it: a pull
request whose head has moved since is refused by the forge, and nothing unjudged is merged.

The record lives in `documents`, under a key of the platform's, like the repository a work item is
fixed to (ADR 0124): written by the orchestrator only. A run cannot write it — its events and its
result are its own word, and the merge reads neither.
"""

from __future__ import annotations

from typing import Any

#: The `documents` key that holds the commit the work item's gates last judged.
JUDGED_KEY = "judged_commit"


def record_judged(item: Any, *, repository: str, branch: str, sha: str) -> None:
    """Leave on the work item the commit its gates just ruled on. Judging the same commit again in
    the same state records the same thing, and writes nothing."""
    record = {"sha": sha, "repository": repository, "branch": branch, "state": item.state}
    documents = dict(item.documents or {})
    if documents.get(JUDGED_KEY) != record:
        item.documents = {**documents, JUDGED_KEY: record}


def judged_commit(item: Any, repository: str) -> str | None:
    """The commit the gates judged in the state the work item is in now, on `repository`.

    `None` when no gate ruled there: a merge transition that carries no gate, whose evaluation never
    ran. A verdict given in an earlier state is not this one's: a step may have written since.
    """
    record = (item.documents or {}).get(JUDGED_KEY)
    if not isinstance(record, dict):
        return None
    if record.get("state") != item.state or record.get("repository") != repository:
        return None
    sha = record.get("sha")
    return sha if isinstance(sha, str) and sha else None


__all__ = ["JUDGED_KEY", "judged_commit", "record_judged"]
