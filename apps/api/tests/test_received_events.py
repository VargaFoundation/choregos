"""The webhook events a project received, and what the platform did with each (S42-06f).

Owner review of 2026-10-28: "we don't see the webhooks, or at least the events received". Each event
of a verified delivery that reaches a project leaves a row; the summary is built from typed fields,
never from what a person wrote.
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


async def _project(client: AsyncClient, slug: str = "abc") -> dict[str, Any]:
    created = await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": slug, "name": slug, "config": {"slug": slug, "org": "varga"}},
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _jira(client: AsyncClient, delivery: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = await client.post(
        "/api/v1/webhooks/jira",
        json=payload,
        headers={"X-Choregos-Secret": SECRET, "X-Atlassian-Webhook-Identifier": delivery},
    )
    assert response.status_code == 202, response.text
    return dict(response.json())


def _labelled(key: str, before: str, after: str) -> dict[str, Any]:
    return {
        "webhookEvent": "jira:issue_updated",
        "user": {"displayName": "Vincent", "emailAddress": "vincent@acme.test"},
        "issue": {"key": key, "fields": {"summary": "Refunds are late", "labels": after.split()}},
        "changelog": {"items": [{"field": "labels", "fromString": before, "toString": after}]},
    }


def _moved(key: str) -> dict[str, Any]:
    return {
        "webhookEvent": "jira:issue_updated",
        "issue": {"key": key, "fields": {"summary": "Refunds are late", "labels": ["agent-ready"]}},
        "changelog": {"items": [{"field": "status", "fromString": "To Do", "toString": "In Progress"}]},
    }


async def _received(client: AsyncClient, project: dict[str, Any], **params: Any) -> dict[str, Any]:
    response = await client.get(f"/api/v1/projects/{project['id']}/received-events", params=params)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def test_each_event_says_what_the_platform_did_with_it(client: AsyncClient, admin: str) -> None:
    project = await _project(client)
    await _jira(client, "d-1", _labelled("ABC-1", "", "agent-ready"))
    await _jira(client, "d-2", _moved("ABC-1"))
    await _jira(client, "d-3", _labelled("ABC-1", "agent-ready", "bug"))
    await _jira(client, "d-4", _labelled("ABC-2", "", "bug"))

    items = (await _received(client, project))["items"]

    outcomes = [(e["work_item_key"], e["event_type"], e["outcome"]) for e in reversed(items)]
    assert outcomes == [
        ("ABC-1", "tracker.item.labeled", "created_work_item"),
        ("ABC-1", "tracker.item.moved", "signalled"),
        ("ABC-1", "tracker.item.labeled", "labels_updated"),
        ("ABC-2", "tracker.item.labeled", "ignored_not_agent_ready"),
    ]
    first = items[-1]
    assert first["source"] == "jira" and first["delivery_id"] == "d-1" and first["work_item_id"]
    assert first["summary"] == "tracker.item.labeled · ABC-1 · label added: agent-ready"
    assert "To Do → In Progress" in items[-2]["summary"]


async def test_nothing_a_person_wrote_is_kept(client: AsyncClient, admin: str) -> None:
    project = await _project(client)
    await _jira(client, "d-1", _labelled("ABC-1", "", "agent-ready"))
    await _jira(
        client,
        "d-2",
        {
            "webhookEvent": "comment_created",
            "issue": {"key": "ABC-1", "fields": {"summary": "Refunds are late"}},
            "comment": {"body": "my password is hunter2", "author": {"displayName": "Vincent"}},
        },
    )

    page = await client.get(f"/api/v1/projects/{project['id']}/received-events")

    assert page.status_code == 200
    for secret in ("hunter2", "Refunds are late", "Vincent", "vincent@acme.test"):
        assert secret not in page.text, secret
    assert next(e["event_type"] for e in page.json()["items"]) == "tracker.item.commented"


async def test_a_delivery_for_no_project_or_seen_twice_leaves_no_row(client: AsyncClient, admin: str) -> None:
    project = await _project(client)
    await _jira(client, "d-1", _labelled("ZZZ-1", "", "agent-ready"))
    await _jira(client, "d-2", _labelled("ABC-1", "", "agent-ready"))
    replay = await _jira(client, "d-2", _labelled("ABC-1", "", "agent-ready"))

    assert replay["duplicate"] is True
    assert [e["work_item_key"] for e in (await _received(client, project))["items"]] == ["ABC-1"]


async def test_the_list_pages_newest_first(client: AsyncClient, admin: str) -> None:
    project = await _project(client)
    for n in range(3):
        await _jira(client, f"d-{n}", _labelled(f"ABC-{n}", "", "bug"))

    first = await _received(client, project, limit=2)
    rest = await _received(client, project, limit=2, cursor=first["meta"]["next_cursor"])

    assert [e["work_item_key"] for e in first["items"]] == ["ABC-2", "ABC-1"]
    assert first["meta"]["has_more"] is True
    assert [e["work_item_key"] for e in rest["items"]] == ["ABC-0"] and rest["meta"]["has_more"] is False


async def test_a_stranger_reads_none(client: AsyncClient, admin: str) -> None:
    project = await _project(client)
    await _jira(client, "d-1", _labelled("ABC-1", "", "agent-ready"))
    await client.get("/api/v1/auth/logout")
    client.cookies.clear()

    refused = await client.get(f"/api/v1/projects/{project['id']}/received-events")

    assert refused.status_code in {401, 403}


async def test_a_jira_project_named_otherwise_sees_its_events(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The owner's case: Jira project XXX, Choregos project yyy (routed by the connector, #1088)."""
    monkeypatch.setenv("CONNECTOR_JIRA_TEST_TOKEN", "t")
    project = await _project(client, "yyy")
    put = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
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

    await _jira(client, "d-1", _labelled("XXX-7", "", "agent-ready"))

    items = (await _received(client, project))["items"]
    assert [(e["work_item_key"], e["outcome"]) for e in items] == [("XXX-7", "created_work_item")]
