"""A ticket that comes back restarts its finished work item (C1, D2C plan §4).

The platform's healthcheck reopens a ticket when a finding comes back (label `recidive`). Until C1 the
work item stayed ended: a trigger delivery for its key answered `already_open`, and a reopened issue only
marked its decisions. A comeback now starts a NEW PASS of the same work item — the next run of its
interpreter id, at its workflow's start state, `recurrence` counted — once, whichever door sees it first.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from .test_triggers import SNOW, _call, _token
from .test_triggers import _project as _desk

REPO = {"full_name": "varga/billing-api"}
KEY = "varga/billing-api#7"
JIRA_KEY = "BILL-42"
SECRET = "s3cret"


def _temporal() -> Any:
    from choregos_api.temporal import get_temporal

    return get_temporal()


async def _ended(project_id: str, key: str, state: str = "deployed_prod") -> str:
    """A work item that went to its end: terminal state, closed, with a pull request."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = WorkItem(
            project_id=project_id,
            tracker_key=key,
            title="Kernel 6.1 is end of life",
            state=state,
            closed_at=datetime.now(UTC),
            pr_url="https://github.com/varga/billing-api/pull/3",
            documents={"deployed_release": "rel-1", "labels": ["agent-ready"]},
        )
        session.add(item)
        await session.flush()
        return str(item.id)


async def _row(item_id: str) -> Any:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, item_id)
        assert item is not None
        session.expunge(item)
        return item


async def _reopenings(item_id: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = await session.execute(select(Event).where(Event.work_item_id == item_id).order_by(Event.ts))
        return [dict(e.payload) for e in rows.scalars() if (e.payload or {}).get("reason") == "reopened"]


async def _outcomes(client: AsyncClient, project_id: str) -> list[str]:
    page = (await client.get(f"/api/v1/projects/{project_id}/received-events")).json()
    return [row["outcome"] for row in reversed(page["items"])]


async def _github(client: AsyncClient, delivery: str, action: str, **extra: Any) -> dict[str, Any]:
    number = int(KEY.rsplit("#", 1)[1])
    body = {
        "action": action,
        "repository": REPO,
        "issue": {"number": number, "title": "Kernel", "state": "open", "labels": [{"name": "agent-ready"}]},
        "sender": {"login": "healthcheck"},
        **extra,
    }
    answer = await client.post(
        "/api/v1/webhooks/github",
        content=json.dumps(body).encode(),
        headers={
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": delivery,
            "content-type": "application/json",
        },
    )
    assert answer.status_code == 202, answer.text
    return dict(answer.json())


# ───────────────────────── GitHub ─────────────────────────


async def test_a_reopened_github_issue_restarts_its_ended_work_item_once(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item_id = await _ended(project["id"], KEY)
    restarted = len(_temporal().restarted)

    await _github(client, "g-1", "reopened")

    item = await _row(item_id)
    assert (item.recurrence, item.state, item.closed_at) == (1, "inbox", None)
    assert item.recurred_at is not None and item.pr_url is None
    assert "deployed_release" not in item.documents and item.documents["labels"] == ["agent-ready"]
    workflow_id = "wi-billing-api-varga_billing-api-7"
    assert _temporal().restarted[restarted:] == [workflow_id]
    assert _temporal().started[workflow_id] == {
        "project_id": project["id"],
        "project_slug": "billing-api",
        "work_item_id": item_id,
        "tracker_key": KEY,
        "resume_from": "inbox",
        "recurrence": 1,
    }
    (event,) = await _reopenings(item_id)
    assert event["from"] == "deployed_prod" and event["to"] == "inbox" and event["recurrence"] == 1
    assert event["door"] == "tracker:github" and event["pr_url"].endswith("/pull/3")

    # The same delivery again, then another one saying the same: the work item runs, nothing restarts.
    assert (await _github(client, "g-1", "reopened"))["duplicate"] is True
    await _github(client, "g-2", "reopened")
    assert _temporal().restarted[restarted:] == [workflow_id]
    assert (await _row(item_id)).recurrence == 1
    assert await _outcomes(client, project["id"]) == ["reopened", "labels_updated"]


async def test_the_start_label_applied_again_restarts_an_ended_work_item(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item_id = await _ended(project["id"], KEY, state="abandoned")
    restarted = len(_temporal().restarted)

    await _github(client, "g-3", "labeled", label={"name": "agent-ready"})

    assert (await _row(item_id)).recurrence == 1
    assert len(_temporal().restarted) == restarted + 1
    assert await _outcomes(client, project["id"]) == ["reopened"]


async def test_an_edit_of_an_ended_issue_restarts_nothing(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    item_id = await _ended(project["id"], KEY)
    restarted = len(_temporal().restarted)

    await _github(client, "g-4", "edited")

    item = await _row(item_id)
    assert item.recurrence == 0 and item.closed_at is not None and item.state == "deployed_prod"
    assert len(_temporal().restarted) == restarted


# ───────────────────────── Jira ─────────────────────────


async def _jira(client: AsyncClient, delivery: str, moves: tuple[str, str], category: str) -> None:
    """A Jira status change: the changelog's move, and the status the issue is in now."""
    left, entered = moves
    payload = {
        "webhookEvent": "jira:issue_updated",
        "user": {"displayName": "healthcheck"},
        "issue": {
            "key": JIRA_KEY,
            "fields": {
                "summary": "Kernel 6.1 is end of life",
                "labels": ["healthcheck", "agent-ready", "recidive"],
                "status": {"name": entered, "statusCategory": {"key": category}},
            },
        },
        "changelog": {"items": [{"field": "status", "fromString": left, "toString": entered}]},
    }
    answer = await client.post(
        "/api/v1/webhooks/jira",
        json=payload,
        headers={"X-Choregos-Secret": SECRET, "X-Atlassian-Webhook-Identifier": delivery},
    )
    assert answer.status_code == 202, answer.text


@pytest.fixture
async def bill(client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A project the Jira key routes to by its slug (no Jira connector: no call to Jira)."""
    from choregos_api.config import get_settings

    monkeypatch.setattr(get_settings(), "generic_webhook_secret", SECRET)
    created = await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": "bill", "name": "bill", "config": {"slug": "bill", "org": "varga"}},
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def test_a_jira_ticket_moved_back_to_work_restarts_its_ended_work_item_once(
    client: AsyncClient, bill: dict[str, Any]
) -> None:
    item_id = await _ended(bill["id"], JIRA_KEY)
    restarted = len(_temporal().restarted)

    # The workflow ends in `Done`; the healthcheck then closes the ticket in a Done status of its own.
    await _jira(client, "j-1", ("Done", "Closed"), "done")
    assert (await _row(item_id)).recurrence == 0, "a move into a Done status is an end, not a comeback"
    assert len(_temporal().restarted) == restarted

    # The finding comes back: the healthcheck moves the ticket to To Do.
    await _jira(client, "j-2", ("Closed", "Todo"), "new")
    item = await _row(item_id)
    assert (item.recurrence, item.state, item.closed_at) == (1, "inbox", None)
    assert _temporal().restarted[restarted:] == ["wi-bill-BILL-42"]

    # Choregos mirrors the new pass's start state; a person moves it along: no second pass.
    await _jira(client, "j-3", ("Todo", "In progress"), "indeterminate")
    assert len(_temporal().restarted) == restarted + 1 and (await _row(item_id)).recurrence == 1


async def test_a_move_between_working_statuses_of_an_ended_jira_ticket_restarts_nothing(
    client: AsyncClient, bill: dict[str, Any]
) -> None:
    item_id = await _ended(bill["id"], JIRA_KEY, state="abandoned")
    await _jira(client, "j-4", ("In review", "In progress"), "indeterminate")
    assert (await _row(item_id)).recurrence == 0


@pytest.mark.parametrize(
    ("payload", "reopened"),
    [
        ({"from_status": "Done", "to_status": "Todo"}, True),
        ({"from_status": "Closed", "to_status": "Todo", "status_category": "new"}, True),
        ({"from_status": "Done", "to_status": "Closed", "status_category": "done"}, False),
        ({"from_status": "In review", "to_status": "In progress"}, False),
        ({"from_status": "Done", "to_status": "Cancelled"}, False),
    ],
)
def test_the_rule_reads_the_category_and_the_workflows_working_statuses(
    payload: dict[str, Any], reopened: bool
) -> None:
    from choregos_api.services.decision_ledger import tracker_says_reopened

    working = ["Todo", "In progress", "In review"]
    assert tracker_says_reopened("tracker.item.moved", payload, ["Done", "Cancelled"], working) is reopened


@pytest.mark.parametrize(
    ("payload", "back"),
    [
        ({"from_status": "Done", "to_status": "Todo", "status_category": "new"}, True),
        ({"from_status": "Done", "to_status": "In progress", "status_category": "indeterminate"}, True),
        # A GitHub board column, a plugin tracker: no category, so no comeback by a column.
        ({"from_status": "Done", "to_status": "Archived"}, False),
    ],
)
def test_a_move_brings_a_work_item_back_only_into_a_to_do_or_in_progress_category(
    payload: dict[str, Any], back: bool
) -> None:
    from choregos_api.services.recurrence import says_back_to_work

    assert says_back_to_work("tracker.item.moved", payload) is back
    assert says_back_to_work("tracker.item.updated", {"reopened": True}) is True


# ───────────────────────── triggers ─────────────────────────


async def _end(project_id: str, key: str) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        item = (
            await session.execute(
                select(WorkItem).where(WorkItem.project_id == project_id, WorkItem.tracker_key == key)
            )
        ).scalar_one()
        item.state, item.closed_at = "done", datetime.now(UTC)


async def test_a_trigger_delivery_for_a_finished_key_restarts_it_and_an_open_key_stays_open(
    client: AsyncClient, admin: str
) -> None:
    project = await _desk(client)
    token = (await _token(client, project, "incident"))["token"]
    first = (await _call(client, "incident", token, SNOW)).json()
    key = first["work_item_key"]

    again = await _call(client, "incident", token, {**SNOW, "number": "INC0010043"})
    assert again.json()["outcome"] == "already_open", "an open key is still answered `already_open`"

    await _end(project["id"], key)
    restarted = len(_temporal().restarted)
    back = await _call(client, "incident", token, {**SNOW, "number": "INC0010044"})
    assert back.status_code == 202, back.text
    assert back.json()["outcome"] == "reopened" and back.json()["work_item_key"] == key
    item = await _row(first["work_item_id"])
    assert (item.recurrence, item.state, item.closed_at) == (1, "triage", None)
    assert len(_temporal().restarted) == restarted + 1

    later = await _call(client, "incident", token, {**SNOW, "number": "INC0010045"})
    assert later.json()["outcome"] == "already_open", "the new pass runs: the next call is a call again"
    assert len(_temporal().restarted) == restarted + 1
    assert await _outcomes(client, project["id"]) == [
        "created_work_item",
        "already_open",
        "reopened",
        "already_open",
    ]


# ───────────────────────── one pass per comeback ─────────────────────────


async def test_a_door_that_read_the_work_item_before_another_pass_claims_nothing(
    project: dict[str, Any],
) -> None:
    """Two doors read the ended work item; one claims the pass, which runs and ends again; the other,
    acting on what it read, must not start a third: the pass is claimed on the recurrence it saw."""
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.recurrence import reopen_work_item

    item_id = await _ended(project["id"], KEY)
    async with session_scope() as late:
        stale = await late.get(WorkItem, item_id)
        owner = await late.get(Project, project["id"])
        assert stale is not None and owner is not None and stale.recurrence == 0
        async with session_scope() as first:
            item = await first.get(WorkItem, item_id)
            assert item is not None
            assert await reopen_work_item(first, owner, item, door="tracker:github", reason="reopened") == 1
        async with session_scope() as end:
            again = await end.get(WorkItem, item_id)
            assert again is not None
            again.state, again.closed_at = "deployed_prod", datetime.now(UTC)
        assert await reopen_work_item(late, owner, stale, door="tracker:sync", reason="stale read") is None
    assert (await _row(item_id)).recurrence == 1
