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

#: The keys a workflow SENDS to an activity (and carries over a continue-as-new) were born French
#: too (S45-39, ADR 0098 §4). A workflow that starts after this patch sends them in English; one
#: that started before keeps sending the French, and a history recorded before replays as it was.
#: The activities read both spellings (`wire_names`), forever.
ENGLISH_PAYLOAD_KEYS = "english-payload-keys"


def named(english: Any, french: Any) -> Any:
    """The activity to schedule: the English one, unless this history predates the patch."""
    return english if workflow.patched(ENGLISH_ACTIVITY_NAMES) else french


def english_payload_keys() -> bool:
    """Whether this run sends English payload keys (`False`: it replays a history that predates them)."""
    return workflow.patched(ENGLISH_PAYLOAD_KEYS)


__all__ = ["ENGLISH_ACTIVITY_NAMES", "ENGLISH_PAYLOAD_KEYS", "english_payload_keys", "named"]
