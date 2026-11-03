"""A tracker card move is logged and never signalled to a workflow (ADR 0102).

A board status change has no Choregos principal behind it: moving a work item is done in the console.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from httpx import AsyncClient

KEY = "varga/billing-api#12"
REPO = {"full_name": "varga/billing-api"}
SECRET = "s3cret"


def _inbound_types() -> list[Any]:
    from choregos_api.temporal import get_temporal

    signals = get_temporal().signals  # type: ignore[attr-defined]
    return [s[2].get("type") for s in signals if s[1] == "inbound"]


async def _github(client: AsyncClient, event: str, delivery: str, body: dict[str, Any]) -> Any:
    return await client.post(
        "/api/v1/webhooks/github",
        content=json.dumps({"sender": {"login": "marie"}, **body}).encode(),
        headers={
            "X-GitHub-Event": event,
            "X-GitHub-Delivery": delivery,
            "content-type": "application/json",
        },
    )


async def _routed_item(client: AsyncClient, project: dict[str, Any]) -> None:
    put = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
        json={"type": "github-issues", "config": {"repo": "varga/billing-api"}},
    )
    assert put.status_code == 200, put.text
    issue = {"number": 12, "title": "Refunds", "labels": [{"name": "agent-ready"}]}
    opened = await _github(client, "issues", "d0", {"action": "opened", "repository": REPO, "issue": issue})
    assert opened.status_code == 202, opened.text


async def test_a_github_card_move_is_not_signalled(client: AsyncClient, project: dict[str, Any]) -> None:
    await _routed_item(client, project)
    before = len(_inbound_types())

    moved = await _github(
        client,
        "projects_v2_item",
        "d1",
        {
            "repository": REPO,
            "work_item_key": KEY,
            "projects_v2_item": {"project_node_id": "PVT_1", "content_node_id": "I_1"},
            "changes": {
                "field_value": {"field_name": "Status", "from": {"name": "Inbox"}, "to": {"name": "Done"}}
            },
        },
    )

    assert moved.status_code == 202, moved.text
    assert "tracker.item.moved" not in _inbound_types()[before:]


async def test_a_jira_status_change_is_not_signalled(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import get_settings

    monkeypatch.setattr(get_settings(), "generic_webhook_secret", SECRET)
    monkeypatch.setenv("CONNECTOR_JIRA_TEST_TOKEN", "t")
    created = await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": "yyy", "name": "yyy", "config": {"slug": "yyy", "org": "varga"}},
    )
    assert created.status_code == 201, created.text
    put = await client.put(
        f"/api/v1/projects/{created.json()['id']}/connectors/tracker",
        json={
            "type": "jira",
            "config": {
                "base_url": "https://acme.atlassian.net",
                "email": "bot@acme.test",
                "project_key": "XXX",
            },
            "secret_refs": {"api_token": "env:CONNECTOR_JIRA_TEST_TOKEN"},
        },
    )
    assert put.status_code == 200, put.text

    async def deliver(delivery: str, changelog: dict[str, Any]) -> Any:
        issue = {
            "key": "XXX-7",
            "self": "https://acme.atlassian.net/rest/api/3/issue/10001",
            "fields": {"summary": "Refunds", "labels": ["agent-ready"], "status": {"name": "Done"}},
        }
        response = await client.post(
            "/api/v1/webhooks/jira",
            json={
                "webhookEvent": "jira:issue_updated",
                "user": {"displayName": "Vincent"},
                "issue": issue,
                "changelog": changelog,
            },
            headers={"X-Choregos-Secret": SECRET, "X-Atlassian-Webhook-Identifier": delivery},
        )
        assert response.status_code == 202, response.text
        return response

    await deliver("j0", {"items": [{"field": "labels", "fromString": "", "toString": "agent-ready"}]})
    before = len(_inbound_types())

    await deliver("j1", {"items": [{"field": "status", "fromString": "To Do", "toString": "Done"}]})

    assert "tracker.item.moved" not in _inbound_types()[before:]


async def test_other_events_still_reach_the_interpreter(client: AsyncClient, project: dict[str, Any]) -> None:
    await _routed_item(client, project)
    before = len(_inbound_types())

    labeled = await _github(
        client,
        "issues",
        "d2",
        {
            "action": "labeled",
            "repository": REPO,
            "issue": {"number": 12, "title": "Refunds", "labels": [{"name": "agent-ready"}]},
            "label": {"name": "urgent"},
        },
    )

    assert labeled.status_code == 202, labeled.text
    assert len(_inbound_types()) > before
