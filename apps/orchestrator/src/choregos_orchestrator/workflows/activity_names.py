# SPDX-License-Identifier: Apache-2.0
"""English activity names, without breaking a single recorded history (S34-02, ADR 0046).

An activity's name travels in the workflow's history. Eight activities were born with French
names; renaming them outright would make every history recorded before — and every workflow in
flight — schedule a name the new code no longer asks for, and replay would fail. So:

- the worker registers BOTH names, on the same implementation (`activities.ALL_ACTIVITIES`);
- workflow code asks for the English name behind the `english-activity-names` patch, and for the
  French one when it replays a history written without that marker.

`workflow.patched` is memoised for a run: a workflow that started before the patch keeps the
French names to its end, one that starts after uses the English ones from its first activity.
"""

from __future__ import annotations

from typing import Any

from temporalio import workflow

ENGLISH_ACTIVITY_NAMES = "english-activity-names"


def named(english: Any, french: Any) -> Any:
    """The activity to schedule: the English one, unless this history predates the patch."""
    return english if workflow.patched(ENGLISH_ACTIVITY_NAMES) else french


__all__ = ["ENGLISH_ACTIVITY_NAMES", "named"]
