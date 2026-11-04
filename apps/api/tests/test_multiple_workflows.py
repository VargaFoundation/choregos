"""A project carries several workflows, one active per name (ADR 0031).

An HR project must hold arrival and departure side by side. Before, publishing `offboarding`
deactivated `onboarding`, and "the workflow of the project" was the highest active version,
whatever its name.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


def _workflow_yaml(name: str, state: str = "request", version: int = 1) -> str:
    return f"""apiVersion: choregos/v1
kind: Workflow
metadata: {{name: {name}, version: {version}}}
actors:
  hr: {{type: human, group: hr}}
states:
  {state}: {{display: Request, kind: wait}}
  done: {{display: Done, terminal: true}}
transitions:
  - {{id: t-do, from: {state}, to: done, by: hr}}
"""


async def _publish(client: AsyncClient, pid: str, name: str, **extra: Any) -> Any:
    return await client.put(
        f"/api/v1/projects/{pid}/workflows/{name}", json={"yaml": _workflow_yaml(name), **extra}
    )


async def test_arrival_and_departure_are_active_together(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    for name in ("onboarding", "offboarding"):
        response = await _publish(client, pid, name)
        assert response.status_code == 200, response.text
    listing = (await client.get(f"/api/v1/projects/{pid}/workflows")).json()
    names = {w["name"] for w in listing}
    assert {"onboarding", "offboarding"} <= names, listing
    # The default did not move: publishing by name does not make it the default.
    default = (await client.get(f"/api/v1/projects/{pid}/workflow")).json()
    assert default["name"] == "default-simple" and default["is_default"] is True


async def test_the_route_name_and_the_yaml_name_match(client: AsyncClient, project: dict[str, Any]) -> None:
    response = await client.put(
        f"/api/v1/projects/{project['id']}/workflows/onboarding", json={"yaml": _workflow_yaml("offboarding")}
    )
    assert response.status_code == 422, response.text


async def test_a_stale_read_version_gets_409(client: AsyncClient, project: dict[str, Any]) -> None:
    pid = project["id"]
    first = (await _publish(client, pid, "onboarding")).json()
    assert (await _publish(client, pid, "onboarding", base_version=first["version"])).status_code == 200
    stale = await _publish(client, pid, "onboarding", base_version=first["version"])
    assert stale.status_code == 409, stale.text


async def test_restoring_creates_the_next_version(client: AsyncClient, project: dict[str, Any]) -> None:
    pid = project["id"]
    v1 = (await _publish(client, pid, "onboarding")).json()
    v2 = (await _publish(client, pid, "onboarding")).json()
    restored = await client.post(
        f"/api/v1/projects/{pid}/workflows/onboarding/versions/{v1['version']}/restore"
    )
    assert restored.status_code == 200, restored.text
    assert restored.json()["version"] == v2["version"] + 1
    versions = (await client.get(f"/api/v1/projects/{pid}/workflows/onboarding/versions")).json()
    assert [v["version"] for v in versions] == [v2["version"] + 1, v2["version"], v1["version"]]
    assert [v["is_active"] for v in versions] == [True, False, False]


async def test_the_default_and_a_routing_target_cannot_be_deactivated(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    for name in ("onboarding", "offboarding", "mobility"):
        assert (await _publish(client, pid, name)).status_code == 200
    routing = {
        "default": "onboarding",
        "rules": [{"when": {"labels_any": ["departure"]}, "workflow": "offboarding"}],
    }
    put = await client.put(f"/api/v1/projects/{pid}/workflow-routing", json=routing)
    assert put.status_code == 200, put.text
    assert (await client.post(f"/api/v1/projects/{pid}/workflows/onboarding/deactivate")).status_code == 409
    assert (await client.post(f"/api/v1/projects/{pid}/workflows/offboarding/deactivate")).status_code == 409
    assert (await client.post(f"/api/v1/projects/{pid}/workflows/mobility/deactivate")).status_code == 204
    names = {w["name"] for w in (await client.get(f"/api/v1/projects/{pid}/workflows")).json()}
    assert "mobility" not in names
    assert (await client.get(f"/api/v1/projects/{pid}/workflow")).json()["name"] == "onboarding"


async def test_a_routing_to_an_unknown_workflow_is_refused(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    response = await client.put(
        f"/api/v1/projects/{project['id']}/workflow-routing",
        json={"default": "default-simple", "rules": [{"when": {"item_type": "Bug"}, "workflow": "phantom"}]},
    )
    assert response.status_code == 422, response.text


# ───────────────────────────── the birth of a work item (S16-03) ─────────────────────────────


async def _two_workflows(client: AsyncClient, pid: str) -> None:
    for name, state in (("onboarding", "arrival"), ("offboarding", "departure")):
        response = await client.put(
            f"/api/v1/projects/{pid}/workflows/{name}", json={"yaml": _workflow_yaml(name, state)}
        )
        assert response.status_code == 200, response.text


async def _birth(client: AsyncClient, pid: str, **fields: Any) -> Any:
    return await client.post(
        f"/api/v1/projects/{pid}/work-items", json={"title": "x", "start": False, **fields}
    )


async def test_a_work_item_is_born_in_the_requested_workflow(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    work_item = (await _birth(client, pid, workflow="offboarding")).json()
    assert (work_item["workflow_name"], work_item["state"]) == ("offboarding", "departure")


async def test_routing_chooses_by_label_and_the_default_otherwise(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    await _two_workflows(client, pid)
    routing = {
        "default": "onboarding",
        "rules": [{"when": {"labels_any": ["departure"]}, "workflow": "offboarding"}],
    }
    assert (await client.put(f"/api/v1/projects/{pid}/workflow-routing", json=routing)).status_code == 200
    routed = (await _birth(client, pid, labels=["hr", "departure"])).json()
    assert (routed["workflow_name"], routed["state"]) == ("offboarding", "departure")
    default = (await _birth(client, pid, labels=["hr"])).json()
    assert (default["workflow_name"], default["state"]) == ("onboarding", "arrival")


async def test_an_unknown_requested_workflow_is_refused(client: AsyncClient, project: dict[str, Any]) -> None:
    response = await _birth(client, project["id"], workflow="phantom")
    assert response.status_code == 422, response.text


async def test_creating_does_not_republish_an_existing_workflow(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """#336: `create_only` refuses a taken name, active or deactivated, and publishes nothing."""
    pid = project["id"]
    assert (await _publish(client, pid, "onboarding", create_only=True)).status_code == 200
    second = await _publish(client, pid, "onboarding", create_only=True)
    assert second.status_code == 409, second.text
    assert "already exists" in second.json()["detail"]
    versions = (await client.get(f"/api/v1/projects/{pid}/workflows/onboarding/versions")).json()
    assert [v["version"] for v in versions] == [1]
    # Deactivated, the name stays taken: recreating it would republish its history.
    assert (await _publish(client, pid, "offboarding", create_only=True)).status_code == 200
    assert (await client.post(f"/api/v1/projects/{pid}/workflows/offboarding/deactivate")).status_code == 204
    assert (await _publish(client, pid, "offboarding", create_only=True)).status_code == 409
    # Without `create_only`, nothing changes: the next version is published.
    assert (await _publish(client, pid, "onboarding")).json()["version"] == 2


async def test_two_simultaneous_creations_one_gets_409(
    client: AsyncClient, project: dict[str, Any], monkeypatch: Any
) -> None:
    """The race the read does not see: the other creation is written between the read and the write."""
    from choregos_api.services import definitions

    pid = project["id"]
    assert (await _publish(client, pid, "onboarding", create_only=True)).status_code == 200

    async def nobody(*_: Any) -> None:
        return None

    monkeypatch.setattr(definitions, "latest_version", nobody)
    second_try = await _publish(client, pid, "onboarding", create_only=True)
    assert second_try.status_code == 409, second_try.text
    monkeypatch.undo()
    versions = (await client.get(f"/api/v1/projects/{pid}/workflows/onboarding/versions")).json()
    assert len(versions) == 1
