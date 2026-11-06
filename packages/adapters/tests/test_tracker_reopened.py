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


# ───────────────────────── C1: when the ticket came back ─────────────────────────


async def test_github_dates_a_reopened_issue_by_its_last_reopened_event_only() -> None:
    from datetime import UTC, datetime

    from choregos_adapters.github.client import GitHubClient
    from choregos_adapters.tracker.github import GitHubTracker

    from ._transport import Wire

    issue = {"title": "Kernel", "state": "open", "labels": []}
    wire = Wire(
        {
            ("GET", f"/repos/{REPO}/issues/12"): [
                (200, {**issue, "state_reason": "reopened"}),
                (200, {**issue, "state_reason": None}),
            ],
            ("GET", f"/repos/{REPO}/issues/12/comments"): (200, []),
            ("GET", f"/repos/{REPO}/issues/12/events"): (
                200,
                [
                    {"event": "reopened", "created_at": "2026-11-01T08:00:00Z"},
                    {"event": "closed", "created_at": "2026-11-02T08:00:00Z"},
                    {"event": "reopened", "created_at": "2026-11-05T08:00:00Z"},
                    {"event": "labeled", "created_at": "2026-11-06T08:00:00Z"},
                ],
            ),
        }
    )
    client = GitHubClient(token="t", base_url="https://api.github.test", client=wire.client())
    tracker = GitHubTracker(client, REPO)

    reopened = await tracker.fetch_item(f"{REPO}#12")
    assert reopened.reopened_at == datetime(2026, 11, 5, 8, 0, tzinfo=UTC)
    plain = await tracker.fetch_item(f"{REPO}#12")
    assert plain.reopened_at is None
    assert wire.sent("GET").count(("GET", f"/repos/{REPO}/issues/12/events")) == 1, "only the reopened one"


async def test_jira_dates_an_open_issue_by_its_status_category_change() -> None:
    from datetime import UTC, datetime

    import httpx
    from choregos_adapters.http import RestClient
    from choregos_adapters.tracker.jira import JiraTracker

    category = {"key": "new"}

    def reply(request: httpx.Request) -> httpx.Response:
        fields = {
            "summary": "Kernel",
            "status": {"name": "To Do", "statusCategory": category},
            "statuscategorychangedate": "2026-11-05T08:00:00.000+0000",
        }
        return httpx.Response(200, json={"key": "BILL-7", "fields": fields, "names": {}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    jira = JiraTracker(RestClient("https://varga.atlassian.net", client=http, service="jira"), "BILL")
    assert (await jira.fetch_item("BILL-7")).reopened_at == datetime(2026, 11, 5, 8, 0, tzinfo=UTC)
    category["key"] = "done"
    assert (await jira.fetch_item("BILL-7")).reopened_at is None, "a Done issue has not come back"


def test_a_jira_move_says_the_category_of_the_status_it_enters() -> None:
    from choregos_adapters.tracker.jira_events import parse_jira_event

    payload = {
        "webhookEvent": "jira:issue_updated",
        "issue": {
            "key": "BILL-7",
            "fields": {"status": {"name": "Closed", "statusCategory": {"key": "done"}}},
        },
        "changelog": {"items": [{"field": "status", "fromString": "Fixed (proof)", "toString": "Closed"}]},
    }
    (event,) = parse_jira_event("j-1", payload)
    assert event.type is InboundEventType.ITEM_MOVED
    assert event.payload["status_category"] == "done"
