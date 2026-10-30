"""A tracker item starts on a project rule, not only on the `agent-ready` label (S51-03, ADR 0113).

A ServiceNow incident in the desk's queue, a Jira bug of a given type: the project declares
`start_rules`, and the first rule whose every condition holds opens and starts the work item of an
item the project does not hold yet. Without rules, only `agent-ready` starts, as before.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

SECRET = "s3cret"
RULES = [{"item_type": "Incident", "states": ["New", "1"]}, {"labels_all": ["payroll", "urgent"]}]


@pytest.fixture(autouse=True)
def _secret(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import get_settings

    monkeypatch.setattr(get_settings(), "generic_webhook_secret", SECRET)


async def _project(client: AsyncClient, rules: list[dict[str, Any]]) -> dict[str, Any]:
    config = {"slug": "abc", "org": "varga", "start_rules": rules}
    created = await client.post(
        "/api/v1/orgs/varga/projects", json={"slug": "abc", "name": "abc", "config": config}
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


def _created(key: str, issue_type: str, status: str, labels: list[str] | None = None) -> dict[str, Any]:
    return {
        "webhookEvent": "jira:issue_created",
        "issue": {
            "key": key,
            "fields": {
                "summary": "Payroll export fails",
                "issuetype": {"name": issue_type},
                "status": {"name": status},
                "labels": labels or [],
            },
        },
    }


async def _jira(client: AsyncClient, delivery: str, payload: dict[str, Any]) -> None:
    response = await client.post(
        "/api/v1/webhooks/jira",
        json=payload,
        headers={"X-Choregos-Secret": SECRET, "X-Atlassian-Webhook-Identifier": delivery},
    )
    assert response.status_code == 202, response.text


async def _outcomes(client: AsyncClient, project: dict[str, Any]) -> list[tuple[str, str]]:
    page = await client.get(f"/api/v1/projects/{project['id']}/received-events")
    assert page.status_code == 200, page.text
    return [(e["work_item_key"], e["outcome"]) for e in reversed(page.json()["items"])]


async def test_an_item_matching_a_rule_starts_without_the_label(client: AsyncClient, admin: str) -> None:
    from choregos_api.temporal import get_temporal

    project = await _project(client, RULES)

    await _jira(client, "d-1", _created("ABC-1", "Incident", "New"))
    await _jira(client, "d-2", _created("ABC-2", "Incident", "In Progress"))
    await _jira(client, "d-3", _created("ABC-3", "Task", "New"))
    await _jira(client, "d-4", _created("ABC-4", "Task", "Open", ["payroll", "urgent"]))
    await _jira(client, "d-5", _created("ABC-5", "Task", "Open", ["payroll"]))

    assert await _outcomes(client, project) == [
        ("ABC-1", "created_work_item"),
        ("ABC-2", "ignored_not_agent_ready"),
        ("ABC-3", "ignored_not_agent_ready"),
        ("ABC-4", "created_work_item"),
        ("ABC-5", "ignored_not_agent_ready"),
    ]
    started = get_temporal().started  # type: ignore[attr-defined]
    assert {k for k in ("ABC-1", "ABC-4") if any(k in w for w in started)} == {"ABC-1", "ABC-4"}, started


async def test_an_item_moved_into_a_rule_s_state_starts_once(client: AsyncClient, admin: str) -> None:
    project = await _project(client, RULES)
    moved = _created("ABC-7", "Incident", "New") | {
        "webhookEvent": "jira:issue_updated",
        "changelog": {"items": [{"field": "status", "fromString": "Draft", "toString": "New"}]},
    }

    await _jira(client, "d-1", _created("ABC-7", "Incident", "Draft"))
    await _jira(client, "d-2", moved)
    await _jira(client, "d-3", moved | {"changelog": {"items": [{"field": "status", "toString": "1"}]}})

    assert await _outcomes(client, project) == [
        ("ABC-7", "ignored_not_agent_ready"),
        ("ABC-7", "created_work_item"),
        ("ABC-7", "signalled"),
    ]


async def test_without_rules_only_agent_ready_starts(client: AsyncClient, admin: str) -> None:
    project = await _project(client, [])

    await _jira(client, "d-1", _created("ABC-1", "Incident", "New"))
    await _jira(client, "d-2", _created("ABC-2", "Incident", "New", ["agent-ready"]))

    assert await _outcomes(client, project) == [
        ("ABC-1", "ignored_not_agent_ready"),
        ("ABC-2", "created_work_item"),
    ]
