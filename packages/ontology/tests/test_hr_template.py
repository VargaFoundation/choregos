# SPDX-License-Identifier: Apache-2.0
"""The `joiners-leavers` template ships its ontology (S20-09): with the plugin active, an HR project is
born with the register of what an arrival opens and what a departure closes.

The register is kept through the ontology's actions, played by the `ActionWorkflow` like any core
action: recording what a workflow has just opened is low risk, the policy decides; recording a
departure closes accesses in the register, a person who authenticated again decides.

What these tests do not prove: that the template's workflows write to the register themselves — they act
on the directory, the device fleet and the badge readers, and nothing makes them do so yet (the daily
reconciliation is to come).

The template's ontology is in French (its object types, properties and actions are the template's
data, read by its workflows): the names quoted here are that data.
"""

from __future__ import annotations

import asyncio
from typing import Any

from httpx import AsyncClient

from .helpers import stored_objects

ARRIVAL = {  # lang: fr-ok - the fields of the template's French ontology
    "matricule": "M-1042",
    "nom": "Léa Martin",
    "email": "lea.martin@acme.test",
    "poste": "Comptable",  # lang: fr-ok
    "manager": "paul.durand@acme.test",
    "date_arrivee": "2026-11-02",
    "contrat": "C-2026-118",
    "nature": "cdi",
}


async def _hr_project(client: AsyncClient) -> dict[str, Any]:
    created = await client.post(
        "/api/v1/orgs/varga/projects",
        json={
            "slug": "rh",
            "name": "RH",
            "template_ref": "joiners-leavers",
            "config": {"slug": "rh", "org": "varga"},
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
    assert (ontology["name"], ontology["version"]) == ("rh-arrivees-departs", "1.0.0")  # lang: fr-ok
    assert set(ontology["object_types"]) == {
        "collaborateur",
        "contrat",
        "account",
        "group",
        "materiel",
        "badge",
    }
    tools = {"collaborateur_search", "badge_porteur", "action_constater_un_depart"}  # lang: fr-ok
    assert tools <= set(ontology["mcp_tools"])
    audit = (await client.get("/api/v1/audit")).json()["items"]
    (entry,) = [a for a in audit if a["action"] == "template.extension.install"]
    assert entry["payload"]["extension"] == "ontology"
    assert entry["payload"]["result"]["name"] == "rh-arrivees-departs"  # lang: fr-ok
    assert not [a for a in audit if a["action"] == "template.extension.skip"]


async def test_the_register_is_kept_through_actions_and_a_departure_waits_for_a_person(
    client: AsyncClient, temporal: Any
) -> None:
    project = await _hr_project(client)
    arrival = await _propose(client, project, "consigner_une_arrivee", params=ARRIVAL)  # lang: fr-ok
    file = await _until(client, project, arrival["proposal"], "succeeded", "failed")
    assert file["status"] == "succeeded", file.get("error")
    (_, lea), *_ = (await stored_objects(project["id"], "collaborateur")).values()
    assert (lea["nom"], lea["statut"]) == ("Léa Martin", "a_venir")
    assert (await stored_objects(project["id"], "contrat"))["C-2026-118"][1]["nature"] == "cdi"

    badge = await _propose(
        client, project, "consigner_le_badge", target=["M-1042"], params={"uid": "04:A2:19:7F"}
    )
    file = await _until(client, project, badge["proposal"], "succeeded", "failed")
    assert file["status"] == "succeeded", file.get("error")
    assert (await stored_objects(project["id"], "badge"))["04:A2:19:7F"][1]["actif"] is True
    assert (await stored_objects(project["id"], "collaborateur"))["M-1042"][1]["statut"] == "present"

    departure = await _propose(
        client,
        project,
        "constater_un_depart",  # lang: fr-ok
        target=["M-1042"],
        params={
            "date_depart": "2027-03-31",
            "upn": "lea.martin@acme.test",
            "badge": "04:A2:19:7F",
            "numero_de_serie": "PF3K9Z",  # lang: fr-ok
        },
    )
    assert departure["status"] == "pending_approval", "a departure waits for a person"
    badges = await stored_objects(project["id"], "badge")
    assert badges["04:A2:19:7F"][1]["actif"] is True, "nothing is closed before"


async def test_nothing_is_recorded_for_a_person_who_left(client: AsyncClient, temporal: Any) -> None:
    from choregos_api.db.session import session_scope
    from choregos_ontology.service.store import ManagedObject

    project = await _hr_project(client)
    arrival = await _propose(client, project, "consigner_une_arrivee", params=ARRIVAL)  # lang: fr-ok
    assert (await _until(client, project, arrival["proposal"], "succeeded", "failed"))[
        "status"
    ] == "succeeded"
    # The departure, already recorded: written here directly — a person's decision has its own test.
    async with session_scope() as session:
        lea = await session.get(ManagedObject, (project["id"], "collaborateur", "M-1042"))
        assert lea is not None
        lea.properties = {**lea.properties, "statut": "parti"}
    refusal = await client.post(
        f"/api/v1/projects/{project['id']}/proposals",
        json={
            "action_type": "consigner_le_badge",
            "target": ["M-1042"],
            "params": {"uid": "04:FF:00:01"},
            "justification": "a badge for a person who left",
        },
    )
    assert refusal.status_code == 422, refusal.text
    assert "pas_parti" in refusal.text
    assert "04:FF:00:01" not in await stored_objects(project["id"], "badge")
