# SPDX-License-Identifier: Apache-2.0
"""The `joiners-leavers` template ships its ontology (S20-09): with the plugin active, an HR project is
born with the register of what an arrival opens and what a departure closes.

The register is kept through the ontology's actions, played by the `ActionWorkflow` like any core
action: recording what a workflow has just opened is low risk, the policy decides; recording a
departure closes accesses in the register, a person who authenticated again decides.

What these tests do not prove: that the template's workflows write to the register themselves — they act
on the directory, the device fleet and the badge readers, and nothing makes them do so yet (the daily
reconciliation is to come).

The template's ontology is in English since 2.0.0 (S39-11): its object types, properties and actions
are the template's data, read by its workflows.
"""

from __future__ import annotations

import asyncio
from typing import Any

from httpx import AsyncClient

from .helpers import stored_objects

ARRIVAL = {
    "employee_id": "M-1042",
    "full_name": "Léa Martin",
    "email": "lea.martin@acme.test",
    "job": "Accountant",
    "manager": "paul.durand@acme.test",
    "start_date": "2026-11-02",
    "contract": "C-2026-118",
    "contract_type": "permanent",
}


async def _hr_project(client: AsyncClient) -> dict[str, Any]:
    created = await client.post(
        "/api/v1/orgs/varga/projects",
        json={
            "slug": "hr",
            "name": "HR",
            "template_ref": "joiners-leavers",
            "config": {"slug": "hr", "org": "varga"},
        },
    )
    assert created.status_code == 201, created.text
    return dict(created.json())


async def _propose(client: AsyncClient, project: dict[str, Any], action: str, **body: Any) -> dict[str, Any]:
    response = await client.post(
        f"/api/v1/projects/{project['id']}/proposals",
        json={"action_type": action, "justification": "the request approved by HR", **body},
    )
    assert response.status_code == 201, response.text
    return dict(response.json())


async def _until(
    client: AsyncClient, project: dict[str, Any], proposal: str, *statuses: str
) -> dict[str, Any]:
    for _ in range(400):
        file = (await client.get(f"/api/v1/projects/{project['id']}/proposals/{proposal}")).json()
        if file["status"] in statuses:
            return dict(file)
        await asyncio.sleep(0.05)
    raise AssertionError(f"{proposal}: {file['status']}, never {statuses} ({file.get('error')})")


async def test_an_hr_project_is_born_with_its_register(client: AsyncClient) -> None:
    project = await _hr_project(client)
    ontology = (await client.get(f"/api/v1/projects/{project['id']}/ontology")).json()
    assert (ontology["name"], ontology["version"]) == ("hr-joiners-leavers", "2.0.0")
    assert set(ontology["object_types"]) == {
        "employee",
        "contract",
        "account",
        "group",
        "device",
        "badge",
    }
    tools = {"employee_search", "badge_holder", "action_record_leaver"}
    assert tools <= set(ontology["mcp_tools"])
    audit = (await client.get("/api/v1/audit")).json()["items"]
    (entry,) = [a for a in audit if a["action"] == "template.extension.install"]
    assert entry["payload"]["extension"] == "ontology"
    assert entry["payload"]["result"]["name"] == "hr-joiners-leavers"
    assert not [a for a in audit if a["action"] == "template.extension.skip"]


async def test_the_register_is_kept_through_actions_and_a_departure_waits_for_a_person(
    client: AsyncClient, temporal: Any
) -> None:
    project = await _hr_project(client)
    arrival = await _propose(client, project, "record_joiner", params=ARRIVAL)
    file = await _until(client, project, arrival["proposal"], "succeeded", "failed")
    assert file["status"] == "succeeded", file.get("error")
    (_, lea), *_ = (await stored_objects(project["id"], "employee")).values()
    assert (lea["full_name"], lea["status"]) == ("Léa Martin", "upcoming")
    assert (await stored_objects(project["id"], "contract"))["C-2026-118"][1]["contract_type"] == "permanent"

    badge = await _propose(client, project, "record_badge", target=["M-1042"], params={"uid": "04:A2:19:7F"})
    file = await _until(client, project, badge["proposal"], "succeeded", "failed")
    assert file["status"] == "succeeded", file.get("error")
    assert (await stored_objects(project["id"], "badge"))["04:A2:19:7F"][1]["active"] is True
    assert (await stored_objects(project["id"], "employee"))["M-1042"][1]["status"] == "present"

    departure = await _propose(
        client,
        project,
        "record_leaver",
        target=["M-1042"],
        params={
            "last_day": "2027-03-31",
            "upn": "lea.martin@acme.test",
            "badge": "04:A2:19:7F",
            "serial_number": "PF3K9Z",
        },
    )
    assert departure["status"] == "pending_approval", "a departure waits for a person"
    badges = await stored_objects(project["id"], "badge")
    assert badges["04:A2:19:7F"][1]["active"] is True, "nothing is closed before"


async def test_nothing_is_recorded_for_a_person_who_left(client: AsyncClient, temporal: Any) -> None:
    from choregos_api.db.session import session_scope
    from choregos_ontology.service.store import ManagedObject

    project = await _hr_project(client)
    arrival = await _propose(client, project, "record_joiner", params=ARRIVAL)
    assert (await _until(client, project, arrival["proposal"], "succeeded", "failed"))[
        "status"
    ] == "succeeded"
    # The departure, already recorded: written here directly — a person's decision has its own test.
    async with session_scope() as session:
        lea = await session.get(ManagedObject, (project["id"], "employee", "M-1042"))
        assert lea is not None
        lea.properties = {**lea.properties, "status": "gone"}
    refusal = await client.post(
        f"/api/v1/projects/{project['id']}/proposals",
        json={
            "action_type": "record_badge",
            "target": ["M-1042"],
            "params": {"uid": "04:FF:00:01"},
            "justification": "a badge for a person who left",
        },
    )
    assert refusal.status_code == 422, refusal.text
    assert "not_gone" in refusal.text
    assert "04:FF:00:01" not in await stored_objects(project["id"], "badge")
