"""A work item filed in Choregos is born in its external tracker first (S39-16, ADR 0059)."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.errors import AdapterError
from choregos_adapters.fakes.tracker import FakeTracker
from choregos_contracts import InboundEvent, InboundEventType
from choregos_core.domain import NewItem
from httpx import AsyncClient
from sqlalchemy import select

ONBOARDING = """apiVersion: choregos/v1
kind: Workflow
metadata:
  name: onboarding
  version: 1
  inputs:
    type: object
    required: [role]
    additionalProperties: false
    properties:
      role: {type: string, minLength: 1}
actors:
  rh: {type: human, group: rh}
states:
  arrivee: {display: Arrivee, kind: wait}
  fait: {display: Fait, terminal: true}
transitions:
  - {id: t-accueillir, from: arrivee, to: fait, by: rh}
budget: {ticket_usd: 4, on_exceeded: arrivee}
"""


class RefusingTracker(FakeTracker):
    async def create_item(self, data: NewItem) -> str:
        self.calls.append(("create_item", data.title))
        raise AdapterError("403 Resource not accessible by integration")


@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch) -> FakeTracker:
    return _install(monkeypatch, FakeTracker("billing-api"))


def _install(monkeypatch: pytest.MonkeyPatch, fake: FakeTracker) -> FakeTracker:
    from choregos_api.services import tickets

    real = tickets.tracker_du_projet

    async def with_the_fake(session: Any, project: Any) -> Any:
        row, _ = await real(session, project)
        return row, (fake if row is not None and row.type not in {"internal", "fake"} else None)

    monkeypatch.setattr(tickets, "tracker_du_projet", with_the_fake)
    return fake


async def _github(client: AsyncClient, project: dict[str, Any]) -> str:
    # The real builder cannot run here (no GitHub App): the test replaces it, the row is real.
    put = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
        json={"type": "github-issues", "config": {"repo": "varga/billing-api"}},
    )
    assert put.status_code == 200, put.text
    published = await client.put(
        f"/api/v1/projects/{project['id']}/workflows/onboarding", json={"yaml": ONBOARDING}
    )
    assert published.status_code == 200, published.text
    return str(project["id"])


async def test_a_github_project_files_the_issue_then_the_work_item(
    client: AsyncClient, project: dict[str, Any], tracker: FakeTracker
) -> None:
    from choregos_api.temporal import get_temporal, interpreter_id

    pid = await _github(client, project)
    created = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={
            "title": "Add SSO",
            "body": "Please.",
            "workflow": "onboarding",
            "fields": {"role": "data"},
            "budget_usd": 1.5,
            "labels": ["team:identity"],
        },
    )
    assert created.status_code == 201, created.text
    key = created.json()["tracker_key"]
    assert key == "billing-api#1" and tracker.calls[0] == ("create_item", key)
    issue = tracker.items[key]
    assert "source:choregos" in issue.labels and "team:identity" in issue.labels
    assert "agent-ready" not in issue.labels
    assert "Filed from Choregos by admin@varga.dev" in issue.body and issue.body.startswith("Please.")
    body = created.json()
    assert body["fields"] == {"role": "data"} and body["budget_usd"] == 1.5
    assert body["url"] == f"https://fake.tracker/{key}"
    assert interpreter_id("billing-api", key) in get_temporal().started  # type: ignore[attr-defined]


async def test_refused_fields_leave_no_issue(
    client: AsyncClient, project: dict[str, Any], tracker: FakeTracker
) -> None:
    pid = await _github(client, project)
    refused = await client.post(
        f"/api/v1/projects/{pid}/work-items", json={"title": "x", "workflow": "onboarding", "fields": {}}
    )
    assert refused.status_code == 422, refused.text
    wider = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "x", "workflow": "onboarding", "fields": {"role": "a"}, "budget_usd": 99},
    )
    assert wider.status_code == 422, wider.text
    assert not [call for call in tracker.calls if call[0] == "create_item"] and not tracker.items


async def test_a_tracker_refusal_is_a_readable_502(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    _install(monkeypatch, RefusingTracker("billing-api"))
    pid = await _github(client, project)
    refused = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "x", "workflow": "onboarding", "fields": {"role": "a"}},
    )
    assert refused.status_code == 502, refused.text
    assert "Resource not accessible by integration" in refused.text and "github-issues" in refused.text
    async with session_scope() as session:
        assert (await session.execute(select(WorkItem))).first() is None


async def test_internal_or_absent_tracker_unchanged(
    client: AsyncClient, project: dict[str, Any], tracker: FakeTracker
) -> None:
    created = await client.post(f"/api/v1/projects/{project['id']}/work-items", json={"title": "Plain"})
    assert created.status_code == 201, created.text
    assert created.json()["tracker_key"] == "BILLING-API-1"
    assert tracker.calls == []


async def test_the_webhook_ignores_the_issue_until_labelled(
    client: AsyncClient, project: dict[str, Any], tracker: FakeTracker
) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.routers.webhooks import _dispatch
    from choregos_api.temporal import get_temporal

    pid = await _github(client, project)
    created = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "Add SSO", "workflow": "onboarding", "fields": {"role": "a"}},
    )
    key = created.json()["tracker_key"]
    started = dict(get_temporal().started)  # type: ignore[attr-defined]
    event = InboundEvent(
        type=InboundEventType.ITEM_CREATED,
        source="github",
        delivery_id="d-1",
        project_slug="billing-api",
        work_item_key=key,
        actor="choregos-bot",
        payload={"labels": tracker.items[key].labels, "title": "Add SSO"},
    )
    async with session_scope() as session:
        await _dispatch(session, [event])
        rows = (await session.execute(select(WorkItem))).scalars().all()
    assert [row.tracker_key for row in rows] == [key]
    assert get_temporal().started == started  # type: ignore[attr-defined]
