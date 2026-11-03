"""A reopened issue says so on its event (ADR 0121 §6, S52-05): the ledger marks the decisions of an
ended work item that came back. GitHub's `reopened` and GitLab's `reopen` were plain updates."""

from __future__ import annotations

from typing import Any

from choregos_adapters.tracker.github_events import parse_github_event
from choregos_adapters.tracker.gitlab_events import parse_gitlab_event
from choregos_contracts import InboundEventType

REPO = "varga/billing"
PROJECT = "varga/billing-api"


def _github(action: str) -> dict[str, Any]:
    return {
        "action": action,
        "issue": {"number": 12, "title": "Credit notes", "state": "open", "labels": []},
        "repository": {"full_name": REPO},
        "sender": {"login": "marie"},
    }


def _gitlab(action: str) -> dict[str, Any]:
    return {
        "object_kind": "issue",
        "project": {"path_with_namespace": PROJECT, "web_url": f"https://gitlab.com/{PROJECT}"},
        "user": {"username": "marie"},
        "object_attributes": {"iid": 12, "title": "Credit notes", "action": action, "state": "opened"},
        "labels": [],
    }


def test_a_reopened_github_issue_is_an_update_that_says_it_reopened() -> None:
    (event,) = parse_github_event("issues", "d-1", _github("reopened"))
    assert event.type is InboundEventType.ITEM_UPDATED
    assert event.payload["reopened"] is True
    assert event.work_item_key == f"{REPO}#12"


def test_a_github_edit_does_not_say_it_reopened() -> None:
    (event,) = parse_github_event("issues", "d-2", _github("edited"))
    assert event.type is InboundEventType.ITEM_UPDATED
    assert "reopened" not in event.payload


def test_a_reopened_gitlab_issue_is_an_update_that_says_it_reopened() -> None:
    (event,) = parse_gitlab_event("Issue Hook", "u-1", _gitlab("reopen"))
    assert event.type is InboundEventType.ITEM_UPDATED
    assert event.payload["reopened"] is True
    assert event.work_item_key == f"{PROJECT}#12"


def test_a_gitlab_update_does_not_say_it_reopened() -> None:
    (event,) = parse_gitlab_event("Issue Hook", "u-2", _gitlab("update"))
    assert event.type is InboundEventType.ITEM_UPDATED
    assert "reopened" not in event.payload
