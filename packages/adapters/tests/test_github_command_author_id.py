"""A `/choregos` comment carries the commenter's stable numeric id, not only the login (S47-14)."""

from __future__ import annotations

from typing import Any

from choregos_adapters.tracker.github_events import parse_github_event
from choregos_contracts import InboundEventType


def _comment(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "action": "created",
        "repository": {"full_name": "varga/billing-api"},
        "issue": {"number": 7, "title": "Credit notes fail"},
        "comment": {"body": "/choregos pause", "user": user},
        "sender": user,
    }


def test_a_command_carries_the_commenter_id() -> None:
    events = parse_github_event("issue_comment", "d1", _comment({"login": "mallory", "id": 1001}))
    decision = next(e for e in events if e.type == InboundEventType.HUMAN_DECISION)
    assert decision.payload["by_id"] == "1001"
    assert decision.payload["by"] == "mallory"


def test_a_comment_without_an_id_carries_an_empty_one() -> None:
    events = parse_github_event("issue_comment", "d2", _comment({"login": "mallory"}))
    decision = next(e for e in events if e.type == InboundEventType.HUMAN_DECISION)
    assert decision.payload["by_id"] == ""
