"""A Jira delivery reaches the project whose tracker connector holds its project key.

The Jira project key (`XXX` in `XXX-42`) is not the Choregos project's slug. The webhook routed by the
slug, so a project named otherwise never saw an event: the only trace was a log line, and the backup
poll hid it by still starting `agent-ready` issues.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

SECRET = "s3cret"


@pytest.fixture(autouse=True)
def _secret(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import get_settings

    monkeypatch.setattr(get_settings(), "generic_webhook_secret", SECRET)
    monkeypatch.setenv("CONNECTOR_JIRA_TEST_TOKEN", "t")


async def _project(client: AsyncClient, slug: str) -> dict[str, Any]:
    created = await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": slug, "name": slug, "config": {"slug": slug, "org": "varga"}},
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _jira(client: AsyncClient, project: dict[str, Any], key: str, site: str) -> None:
    put = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
        json={
            "type": "jira",
            "config": {
                "base_url": f"https://{site}.atlassian.net",
                "email": "bot@acme.test",
                "project_key": key,
            },
            "secret_refs": {"api_token": "env:CONNECTOR_JIRA_TEST_TOKEN"},
        },
    )
    assert put.status_code == 200, put.text


async def _deliver(client: AsyncClient, key: str, site: str, delivery: str) -> dict[str, Any]:
    payload = {
        "webhookEvent": "jira:issue_updated",
        "user": {"displayName": "Vincent"},
        "issue": {
            "key": key,
            "self": f"https://{site}.atlassian.net/rest/api/3/issue/10001",
            "fields": {"summary": "Refunds", "labels": ["agent-ready"]},
        },
        "changelog": {"items": [{"field": "labels", "fromString": "", "toString": "agent-ready"}]},
    }
    response = await client.post(
        "/api/v1/webhooks/jira",
        json=payload,
        headers={"X-Choregos-Secret": SECRET, "X-Atlassian-Webhook-Identifier": delivery},
    )
    assert response.status_code == 202, response.text
    return dict(response.json())


async def _keys(client: AsyncClient, project: dict[str, Any]) -> list[str]:
    page = (await client.get(f"/api/v1/projects/{project['id']}/work-items")).json()
    return [item["tracker_key"] for item in page["items"]]


async def test_an_event_reaches_the_project_whose_connector_holds_its_key(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client, "yyy")
    await _jira(client, project, "XXX", "acme")

    ack = await _deliver(client, "XXX-7", "acme", "d-1")

    assert ack["events"] == 1
    assert await _keys(client, project) == ["XXX-7"], "the agent-ready issue starts a work item in YYY"


async def test_the_jira_site_tells_two_projects_with_the_same_key_apart(
    client: AsyncClient, admin: str
) -> None:
    acme = await _project(client, "billing")
    globex = await _project(client, "invoicing")
    await _jira(client, acme, "BILL", "acme")
    await _jira(client, globex, "BILL", "globex")

    await _deliver(client, "BILL-3", "globex", "d-2")

    assert await _keys(client, globex) == ["BILL-3"]
    assert await _keys(client, acme) == []


async def test_a_key_held_twice_on_the_same_site_routes_nothing(client: AsyncClient, admin: str) -> None:
    first = await _project(client, "ops")
    second = await _project(client, "bill")
    await _jira(client, first, "BILL", "acme")
    await _jira(client, second, "BILL", "acme")

    ack = await _deliver(client, "BILL-4", "acme", "d-3")

    assert ack["events"] == 0, "ambiguous: never routed, not even to the project whose slug is `bill`"
    assert await _keys(client, first) == [] and await _keys(client, second) == []


async def test_without_a_jira_connector_the_slug_still_routes(client: AsyncClient, admin: str) -> None:
    project = await _project(client, "abc")

    await _deliver(client, "ABC-1", "acme", "d-4")

    assert await _keys(client, project) == ["ABC-1"], "the former rule stays for a project without one"
