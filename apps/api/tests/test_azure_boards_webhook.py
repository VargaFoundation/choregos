"""An Azure DevOps service-hook delivery is authenticated, then reaches the project whose Azure Boards
connector holds its team project (S13-07).

The deliveries are written from Microsoft's service-hook reference: they prove the platform's side of
the exchange, not that a live organisation sends exactly this.
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


async def _azure(
    client: AsyncClient,
    project: dict[str, Any],
    team_project: str,
    organisation: str = "acme",
    **extra: Any,
) -> None:
    put = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
        json={
            "type": "azure-boards",
            "config": {
                "base_url": f"https://dev.azure.com/{organisation}",
                "project": team_project,
                **extra,
            },
            "secret_refs": {"pat": "env:CONNECTOR_AZURE_TEST_PAT"},
        },
    )
    assert put.status_code == 200, put.text


def payload(
    item: int,
    team_project: str,
    organisation: str,
    *,
    area: str | None = None,
    tags: str = "agent-ready",
    notification: str = "n-1",
) -> dict[str, Any]:
    return {
        "notificationId": notification,
        "eventType": "workitem.updated",
        "resource": {
            "id": 3,
            "workItemId": item,
            "revisedBy": {"displayName": "Vincent"},
            "fields": {"System.Tags": {"oldValue": "", "newValue": tags}},
            "revision": {
                "fields": {
                    "System.TeamProject": team_project,
                    "System.Title": "Refunds",
                    "System.State": "New",
                    "System.WorkItemType": "Task",
                    "System.Tags": tags,
                    "System.AreaPath": area or team_project,
                }
            },
        },
        "resourceContainers": {"collection": {"baseUrl": f"https://dev.azure.com/{organisation}/"}},
    }


async def _deliver(
    client: AsyncClient, body: dict[str, Any], headers: dict[str, str] | None = None
) -> dict[str, Any]:
    response = await client.post("/api/v1/webhooks/azure-boards", json=body, headers=headers or basic(SECRET))
    assert response.status_code == 202, response.text
    return dict(response.json())


async def _keys(client: AsyncClient, project: dict[str, Any]) -> list[str]:
    page = (await client.get(f"/api/v1/projects/{project['id']}/work-items")).json()
    return [item["tracker_key"] for item in page["items"]]


async def test_a_delivery_needs_the_shared_secret_in_basic_auth_or_a_header(
    client: AsyncClient, admin: str
) -> None:
    body = payload(1, "Billing", "acme")
    for headers in (basic("wrong"), {"X-Choregos-Secret": "wrong"}, {}):
        refused = await client.post("/api/v1/webhooks/azure-boards", json=body, headers=headers)
        assert refused.status_code == 401, headers
    assert (await _deliver(client, body, basic(SECRET)))["accepted"] is True
    assert (
        await _deliver(
            client, payload(1, "Billing", "acme", notification="n-2"), {"X-Choregos-Secret": SECRET}
        )
    )["accepted"] is True


async def test_an_event_reaches_the_project_whose_connector_holds_the_team_project(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client, "yyy")
    await _azure(client, project, "Billing")

    ack = await _deliver(client, payload(7, "Billing", "acme"))

    assert ack["events"] == 1
    assert await _keys(client, project) == ["7"], "the agent-ready item starts a work item in YYY"


async def test_a_redelivery_is_not_dispatched_twice(client: AsyncClient, admin: str) -> None:
    project = await _project(client, "yyy")
    await _azure(client, project, "Billing")
    await _deliver(client, payload(7, "Billing", "acme", notification="same"))

    again = await _deliver(client, payload(7, "Billing", "acme", notification="same"))

    assert again["duplicate"] is True
    assert await _keys(client, project) == ["7"]


async def test_the_organisation_tells_two_projects_with_the_same_team_project_apart(
    client: AsyncClient, admin: str
) -> None:
    acme = await _project(client, "billing")
    globex = await _project(client, "invoicing")
    await _azure(client, acme, "Billing", "acme")
    await _azure(client, globex, "Billing", "globex")

    await _deliver(client, payload(3, "Billing", "globex"))

    assert await _keys(client, globex) == ["3"]
    assert await _keys(client, acme) == []


async def test_the_area_path_then_the_tags_tell_projects_on_one_team_project_apart(
    client: AsyncClient, admin: str
) -> None:
    team_a = await _project(client, "team-a")
    team_b = await _project(client, "team-b")
    rest = await _project(client, "rest")
    await _azure(client, team_a, "Billing", area_path="Billing\\Team A")
    await _azure(client, team_b, "Billing", area_path="Billing\\Team B", match_labels=["iris"])
    await _azure(client, rest, "Billing")

    await _deliver(client, payload(11, "Billing", "acme", area="Billing\\Team A\\Sub", notification="a"))
    await _deliver(client, payload(12, "Billing", "acme", area="Billing\\Team B", notification="b"))
    await _deliver(client, payload(13, "Billing", "acme", area="Billing\\Other", notification="c"))

    assert await _keys(client, team_a) == ["11"]
    assert await _keys(client, team_b) == ["12"]
    assert await _keys(client, rest) == ["13"], "the connector without an area takes the rest"


async def test_a_team_project_held_twice_with_nothing_to_tell_them_apart_routes_nothing(
    client: AsyncClient, admin: str
) -> None:
    first = await _project(client, "ops")
    second = await _project(client, "billing")
    await _azure(client, first, "Billing")
    await _azure(client, second, "Billing")

    ack = await _deliver(client, payload(4, "Billing", "acme"))

    assert ack["events"] == 0, "ambiguous: never routed, not even to the project whose slug matches"
    assert await _keys(client, first) == [] and await _keys(client, second) == []


async def test_without_a_connector_the_lowercased_team_project_still_routes(
    client: AsyncClient, admin: str
) -> None:
    project = await _project(client, "abc")

    await _deliver(client, payload(1, "ABC", "acme"))

    assert await _keys(client, project) == ["1"]


async def test_two_subscriptions_reusing_one_notification_id_are_both_delivered(
    client: AsyncClient, admin: str
) -> None:
    """`notificationId` counts per subscription: only with its `subscriptionId` (or the event GUID) is it a
    delivery's identity, so another subscription's first delivery is not a redelivery."""
    project = await _project(client, "yyy")
    await _azure(client, project, "Billing")
    first = {**payload(7, "Billing", "acme", notification="1"), "subscriptionId": "sub-a"}
    other = {**payload(8, "Billing", "acme", notification="1"), "subscriptionId": "sub-b"}

    assert (await _deliver(client, first))["duplicate"] is False
    assert (await _deliver(client, other))["duplicate"] is False
    assert sorted(await _keys(client, project)) == ["7", "8"]
    assert (await _deliver(client, first))["duplicate"] is True, "a true redelivery is still dropped"

    guid_a = {**payload(9, "Billing", "acme", notification="1"), "subscriptionId": "sub-a", "id": "guid-1"}
    guid_b = {**payload(10, "Billing", "acme", notification="1"), "subscriptionId": "sub-a", "id": "guid-2"}
    assert (await _deliver(client, guid_a))["duplicate"] is False
    assert (await _deliver(client, guid_b))["duplicate"] is False, "the event GUID is preferred"
    assert (await _deliver(client, guid_a))["duplicate"] is True
