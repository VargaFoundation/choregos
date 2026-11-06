"""The tracker catch-up restarts an ended work item whose ticket came back (C1, D2C plan §4).

A webhook can be lost; the catch-up (`reconcile_tracker`) is then the only one to see that the platform's
healthcheck reopened a ticket. It used to skip every work item that had ended. It now reads such a ticket
again, through the real GitHub and Jira adapters (on a simulated HTTP transport), and starts its new pass
when the tracker dates a return to work after the end — once: a second pass finds the work item running.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from choregos_adapters.github.client import GitHubClient
from choregos_adapters.http import RestClient
from choregos_adapters.tracker.github import GitHubTracker
from choregos_adapters.tracker.jira import JiraTracker
from choregos_orchestrator.activities import tracker as tracker_activities
from choregos_orchestrator.train_client import clear_fake_signals, fake_restarts, fake_starts

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

REPO = "varga/billing-api"
NUMBER = 123
ENDED = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)


async def _end(setup: Fixture, key: str | None = None, state: str = "deployed_prod") -> str:
    """The fixture's work item (or a new one under `key`) at its end, on 2 November."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        if key is None:
            item = await session.get(WorkItem, setup.work_item_id)
            assert item is not None
        else:
            item = WorkItem(project_id=setup.project_id, tracker_key=key, title="Kernel 6.1 is end of life")
            session.add(item)
        item.state, item.closed_at = state, ENDED
        await session.flush()
        return str(item.id)


async def _item(item_id: str) -> Any:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, item_id)
        assert item is not None
        session.expunge(item)
        return item


async def _pass(setup: Fixture) -> dict[str, Any]:
    return await tracker_activities.reconcile_tracker({"project_slug": setup.project_slug})


# ───────────────────────── GitHub ─────────────────────────


def _github(issue: dict[str, Any], events: list[dict[str, Any]]) -> GitHubTracker:
    """GitHub's REST answers for one issue: the open `agent-ready` search, the issue, its comments and
    its events."""

    def reply(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == f"/repos/{REPO}/issues":
            return httpx.Response(200, json=[{"number": NUMBER, **issue}])
        if path == f"/repos/{REPO}/issues/{NUMBER}":
            return httpx.Response(200, json={"number": NUMBER, **issue})
        if path == f"/repos/{REPO}/issues/{NUMBER}/comments":
            return httpx.Response(200, json=[])
        if path == f"/repos/{REPO}/issues/{NUMBER}/events":
            return httpx.Response(200, json=events)
        return httpx.Response(404, json={"message": "Not Found"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    client = GitHubClient(token="t", base_url="https://api.github.test", client=http)
    return GitHubTracker(client, REPO)


def _issue(state_reason: str | None) -> dict[str, Any]:
    return {
        "title": "Kernel 6.1 is end of life",
        "state": "open",
        "state_reason": state_reason,
        "labels": [{"name": "agent-ready"}, {"name": "recidive"}],
    }


def _at(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


async def test_a_github_issue_reopened_after_the_end_restarts_the_work_item_once(setup: Fixture) -> None:
    clear_fake_signals()
    item_id = await _end(setup)
    events = [
        {"event": "closed", "created_at": _at(ENDED + timedelta(hours=1))},
        {"event": "reopened", "created_at": _at(ENDED + timedelta(days=3))},
    ]
    setup.adapters.tracker = _github(_issue("reopened"), events)

    first = await _pass(setup)
    assert first["reopened"] == [setup.tracker_key], first
    item = await _item(item_id)
    assert (item.recurrence, item.state, item.closed_at) == (1, "inbox", None)
    workflow_id = "wi-billing-api-varga_billing-api-123"
    assert fake_restarts() == [workflow_id]
    assert (
        fake_starts()[workflow_id]["recurrence"] == 1 and fake_starts()[workflow_id]["resume_from"] == "inbox"
    )

    second = await _pass(setup)
    assert second["reopened"] == [] and second["started"] == [], second
    assert fake_restarts() == [workflow_id], "reconciling twice changes nothing more"
    assert (await _item(item_id)).recurrence == 1


async def test_an_issue_reopened_before_the_end_restarts_nothing(setup: Fixture) -> None:
    """GitHub keeps `state_reason: reopened` while the issue stays open: an old reopening is no comeback."""
    clear_fake_signals()
    item_id = await _end(setup)
    old = [
        {"event": "reopened", "created_at": _at(ENDED - timedelta(days=1))},
        # The platform's own label at the end is an event of the issue too, and no comeback.
        {"event": "labeled", "created_at": _at(ENDED + timedelta(hours=1))},
    ]
    setup.adapters.tracker = _github(_issue("reopened"), old)

    assert (await _pass(setup))["reopened"] == []
    assert (await _item(item_id)).recurrence == 0 and fake_restarts() == []


async def test_an_ended_work_item_whose_issue_stayed_open_is_left_alone(setup: Fixture) -> None:
    clear_fake_signals()
    item_id = await _end(setup)
    setup.adapters.tracker = _github(_issue(None), [])

    assert (await _pass(setup))["reopened"] == []
    item = await _item(item_id)
    assert item.recurrence == 0 and item.closed_at is not None and fake_restarts() == []


# ───────────────────────── Jira ─────────────────────────


def _jira(status: str, category: str, changed: datetime) -> JiraTracker:
    """Jira's REST answers for BILL-7: the candidates search, then the issue."""

    def reply(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/rest/api/3/search/jql":
            return httpx.Response(200, json={"issues": [{"key": "BILL-7"}]})
        if path == "/rest/api/3/issue/BILL-7":
            fields = {
                "summary": "Kernel 6.1 is end of life",
                "labels": ["healthcheck", "agent-ready", "recidive"],
                "status": {"name": status, "statusCategory": {"key": category}},
                "statuscategorychangedate": changed.strftime("%Y-%m-%dT%H:%M:%S.000+0000"),
            }
            return httpx.Response(200, json={"key": "BILL-7", "fields": fields, "names": {}})
        return httpx.Response(404, json={"errorMessages": ["not found"]})

    http = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    client = RestClient(
        "https://varga.atlassian.net", auth=("bot@varga.dev", "t"), client=http, service="jira"
    )
    return JiraTracker(client, "BILL")


async def test_a_jira_ticket_back_in_to_do_after_the_end_restarts_the_work_item_once(setup: Fixture) -> None:
    clear_fake_signals()
    item_id = await _end(setup, "BILL-7")
    setup.adapters.tracker = _jira("Todo", "new", ENDED + timedelta(days=2))

    first = await _pass(setup)
    assert first["reopened"] == ["BILL-7"], first
    item = await _item(item_id)
    assert (item.recurrence, item.state, item.closed_at) == (1, "inbox", None)
    assert fake_restarts() == ["wi-billing-api-BILL-7"]

    second = await _pass(setup)
    assert second["reopened"] == [] and fake_restarts() == ["wi-billing-api-BILL-7"], second
    assert (await _item(item_id)).recurrence == 1


@pytest.mark.parametrize(
    ("status", "category", "changed"),
    [
        # The platform's own end, mirrored late into a terminal state's status: no comeback.
        ("Cancelled", "indeterminate", ENDED + timedelta(hours=1)),
        # Back in To Do before the end: the comeback is older than the pass that ended.
        ("Todo", "new", ENDED - timedelta(days=1)),
        # Within the margin a tracker's clock may be off by.
        ("Todo", "new", ENDED + timedelta(minutes=2)),
    ],
    ids=["own-terminal-mirror", "before-the-end", "within-the-clock-margin"],
)
async def test_a_jira_ticket_that_did_not_come_back_restarts_nothing(
    setup: Fixture, status: str, category: str, changed: datetime
) -> None:
    clear_fake_signals()
    item_id = await _end(setup, "BILL-7", state="abandoned")
    setup.adapters.tracker = _jira(status, category, changed)

    assert (await _pass(setup))["reopened"] == []
    assert (await _item(item_id)).recurrence == 0 and fake_restarts() == []


async def test_the_fake_tracker_scripts_a_comeback(setup: Fixture) -> None:
    """`FakeTracker.reopen` dates the comeback as GitHub does: the fakes play the catch-up too."""
    clear_fake_signals()
    item_id = await _end(setup)
    tracker = setup.adapters.tracker
    tracker.items[setup.tracker_key].labels = ["agent-ready"]
    assert (await _pass(setup))["reopened"] == [], "still open, never reopened: no comeback"

    event = tracker.reopen(setup.tracker_key)
    assert event.payload["reopened"] is True
    assert (await _pass(setup))["reopened"] == [setup.tracker_key]
    assert (await _item(item_id)).recurrence == 1
