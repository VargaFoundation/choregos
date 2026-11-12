"""What the archive holds of an action that ends in doubt (S65-14, ADR 0149 §5).

`test_replay.py` replays every archived history against the current code. This says what the two
histories of the in-doubt branch prove, so that neither can be swapped for one that proves less:

- `action-in-doubt-S65-14`: the branch itself, under its marker. No compensation is scheduled, and
  the action is closed `in_doubt`;
- `action-in-doubt-before-the-patch-S65-14`: the same failure met by a workflow whose code has no
  such branch (an activity of this version answering a workflow task of the previous one). No marker:
  it took the failure branch, and replays only while the branch stays behind `workflow.patched`.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any

HISTORIES = Path(__file__).parent / "histories"
MARKER = "action-in-doubt"


def _events(name: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = json.loads((HISTORIES / f"{name}.json").read_text(encoding="utf-8"))[
        "events"
    ]
    return events


def _decoded(payload: dict[str, Any]) -> Any:
    return json.loads(base64.b64decode(payload["data"]))


def _patches(events: list[dict[str, Any]]) -> set[str]:
    found = set()
    for event in events:
        marker = event.get("markerRecordedEventAttributes")
        if marker and marker["markerName"] == "core_patch":
            found.add(_decoded(marker["details"]["patch-data"]["payloads"][0])["id"])
    return found


def _scheduled(events: list[dict[str, Any]]) -> list[tuple[str, Any]]:
    """Each activity the workflow scheduled, with what it sent."""
    return [
        (
            event["activityTaskScheduledEventAttributes"]["activityType"]["name"],
            _decoded(event["activityTaskScheduledEventAttributes"]["input"]["payloads"][0]),
        )
        for event in events
        if "activityTaskScheduledEventAttributes" in event
    ]


def _failures(events: list[dict[str, Any]]) -> list[tuple[str, bool]]:
    """The type of each activity failure, and whether it was final."""
    found = []
    for event in events:
        failed = event.get("activityTaskFailedEventAttributes")
        if failed:
            info = failed["failure"]["applicationFailureInfo"]
            found.append((info["type"], bool(info.get("nonRetryable"))))
    return found


def test_the_patch_is_declared_under_the_id_the_archive_holds() -> None:
    from choregos_orchestrator.workflows import action

    assert action.IN_DOUBT_PATCH == MARKER, "a patch id is never renamed: histories hold it"


def test_the_in_doubt_branch_is_archived_under_its_marker_and_compensates_nothing() -> None:
    events = _events("action-in-doubt-S65-14")
    assert MARKER in _patches(events)
    scheduled = _scheduled(events)
    assert [name for name, _ in scheduled] == [
        "load_action",
        "execute_effect",
        "execute_effect",
        "close_action",
    ]
    assert scheduled[-1][1]["status"] == "in_doubt" and "compensations" not in scheduled[-1][1]
    assert _failures(events) == [("EffectInDoubt", True)], "one attempt, final: never retried"


def test_a_history_from_before_the_marker_took_the_failure_branch() -> None:
    events = _events("action-in-doubt-before-the-patch-S65-14")
    assert MARKER not in _patches(events)
    scheduled = _scheduled(events)
    assert [name for name, _ in scheduled] == [
        "load_action",
        "execute_effect",
        "execute_effect",
        "compensate_effect",
        "close_action",
    ]
    assert scheduled[-1][1]["status"] == "failed", "what that code asked for; the activity closed in doubt"
    assert _failures(events) == [("EffectInDoubt", True)]
