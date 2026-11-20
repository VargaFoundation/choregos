"""An Azure DevOps service-hook delivery of a pull request or a build is authenticated, then reaches the
project whose Azure Repos or Azure Pipelines connector holds its team project (S13-08).

The deliveries are written from Microsoft's service-hook reference: they prove the platform's side of the
exchange, not that a live organisation sends exactly this.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest
from httpx import AsyncClient

SECRET = "s3cret"


@pytest.fixture(autouse=True)
def _secret(client: AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from tranibor_api.config import get_settings

    monkeypatch.setattr(get_settings(), "generic_webhook_secret", SECRET)
    monkeypatch.setenv("CONNECTOR_AZURE_TEST_PAT", "pat")


def basic(password: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(f"hook:{password}".encode()).decode()}


async def _project(client: AsyncClient, slug: str) -> dict[str, Any]:
    created = await client.post(
        "/api/v1/orgs/northwind/projects",
        json={"slug": slug, "name": slug, "config": {"slug": slug, "org": "northwind"}},
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _connector(
    client: AsyncClient, project: dict[str, Any], kind: str, team_project: str, **extra: Any
) -> None:
    put = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/{kind}",
        json={
            "type": "azure-repos" if kind == "scm" else "azure-pipelines",
            "config": {"base_url": "https://dev.azure.com/acme", "project": team_project, **extra},
            "secret_refs": {"pat": "env:CONNECTOR_AZURE_TEST_PAT"},
        },
    )
    assert put.status_code == 200, put.text


def pull_request(
    team_project: str,
    repository: str = "billing-api",
    *,
    notification: str = "n-1",
    branch: str = "choregos/7-fix",
) -> dict[str, Any]:
    return {
        "notificationId": notification,
        "eventType": "git.pullrequest.created",
        "resource": {
            "pullRequestId": 5,
            "status": "active",
            "sourceRefName": f"refs/heads/{branch}",
            "targetRefName": "refs/heads/main",
            "lastMergeSourceCommit": {"commitId": "a" * 40},
            "repository": {"name": repository, "project": {"name": team_project}},
        },
        "resourceContainers": {"collection": {"baseUrl": "https://dev.azure.com/acme/"}},
    }


def build_complete(team_project: str, *, notification: str = "b-1") -> dict[str, Any]:
    return {
        "notificationId": notification,
        "eventType": "build.complete",
        "resource": {
            "id": 55,
            "result": "succeeded",
            "sourceBranch": "refs/heads/choregos/7-fix",
            "sourceVersion": "a" * 40,
            "definition": {"id": 7},
            "project": {"name": team_project},
        },
    }


async def _deliver(
    client: AsyncClient, body: dict[str, Any], headers: dict[str, str] | None = None
) -> dict[str, Any]:
    response = await client.post("/api/v1/webhooks/azure-devops", json=body, headers=headers or basic(SECRET))
    assert response.status_code == 202, response.text
    return dict(response.json())


async def _received(client: AsyncClient, project: dict[str, Any]) -> list[tuple[str, str | None, str]]:
    page = (await client.get(f"/api/v1/projects/{project['id']}/received-events")).json()
    return [(e["event_type"], e["work_item_key"], e["outcome"]) for e in page["items"]]


async def test_a_delivery_needs_the_shared_secret_in_basic_auth_or_a_header(
    client: AsyncClient, admin: str
) -> None:
    body = pull_request("Billing")
    for headers in (basic("wrong"), {"X-Choregos-Secret": "wrong"}, {}):
        refused = await client.post("/api/v1/webhooks/azure-devops", json=body, headers=headers)
        assert refused.status_code == 401, headers
    assert (await _deliver(client, body, basic(SECRET)))["accepted"] is True
    assert (
        await _deliver(client, pull_request("Billing", notification="n-2"), {"X-Choregos-Secret": SECRET})
    )["accepted"] is True


async def test_a_pull_request_event_reaches_the_project_whose_scm_connector_holds_the_team_project(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client, "yyy")
    await _connector(client, project, "scm", "Billing", repository="billing-api")

    ack = await _deliver(client, pull_request("Billing"))

    assert ack["events"] == 0, "no work item 7 yet: the event is recorded, nothing is signalled"
    assert await _received(client, project) == [("scm.pr.opened", "7", "ignored_not_agent_ready")]


async def test_a_build_event_reaches_the_project_whose_pipelines_connector_holds_the_team_project(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client, "zzz")
    await _connector(client, project, "ci", "Billing")

    await _deliver(client, build_complete("Billing"))

    assert await _received(client, project) == [("ci.run.succeeded", "7", "ignored_not_agent_ready")]


async def test_a_redelivery_is_not_dispatched_twice(client: AsyncClient, admin: str) -> None:
    project = await _project(client, "yyy")
    await _connector(client, project, "scm", "Billing", repository="billing-api")
    await _deliver(client, pull_request("Billing", notification="same"))

    again = await _deliver(client, pull_request("Billing", notification="same"))

    assert again["duplicate"] is True
    assert len(await _received(client, project)) == 1


async def test_the_repository_tells_two_projects_with_the_same_team_project_apart(
    client: AsyncClient, admin: str
) -> None:
    api = await _project(client, "billing-api")
    web = await _project(client, "billing-web")
    await _connector(client, api, "scm", "Billing", repository="billing-api")
    await _connector(client, web, "scm", "Billing", repository="billing-web")

    await _deliver(client, pull_request("Billing", "billing-web"))

    assert len(await _received(client, web)) == 1
    assert await _received(client, api) == []


async def test_a_project_with_both_connectors_is_one_project_not_an_ambiguity(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client, "both")
    await _connector(client, project, "scm", "Billing", repository="billing-api")
    await _connector(client, project, "ci", "Billing")

    await _deliver(client, build_complete("Billing"))

    assert len(await _received(client, project)) == 1


async def test_a_team_project_held_twice_with_nothing_to_tell_them_apart_routes_nothing(
    client: AsyncClient, admin: str
) -> None:
    first = await _project(client, "ops")
    second = await _project(client, "billing")
    await _connector(client, first, "ci", "Billing")
    await _connector(client, second, "ci", "Billing")

    ack = await _deliver(client, build_complete("Billing"))

    assert ack["events"] == 0
    assert await _received(client, first) == [] and await _received(client, second) == []


async def test_without_a_connector_the_lowercased_team_project_is_the_slug(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client, "abc")

    await _deliver(client, pull_request("ABC"))

    assert len(await _received(client, project)) == 1


async def test_an_event_the_platform_does_not_read_is_accepted_and_ignored(
    client: AsyncClient, admin: str
) -> None:
    ack = await _deliver(client, {"notificationId": "x", "eventType": "git.push", "resource": {}})
    assert ack == {"accepted": True, "duplicate": False, "events": 0}
