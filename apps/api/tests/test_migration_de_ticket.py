"""`migrate` : l'API lit et vérifie la définition cible, l'interpréteur la reçoit (ADR 0031, S16-05).

L'API transmettait `workflow_def_id` seul, et l'interpréteur — qui ne fait aucune entrée/sortie —
validait le message de contrôle lui-même comme une définition de workflow : le ticket mourait. Une
migration se vérifie désormais ici, avant tout signal : la définition appartient au projet, l'état
courant du ticket y existe ou y est mappé, et le ticket n'est pas occupé.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

ARRIVEE = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: onboarding, version: 1}
actors:
  rh: {type: human, group: rh}
states:
  demande: {display: Demande, kind: wait}
  fait: {display: Fait, terminal: true}
transitions:
  - {id: t-faire, from: demande, to: fait, by: rh}
"""


async def _ticket(client: AsyncClient, project: dict[str, Any]) -> dict[str, Any]:
    tracker = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert tracker.status_code == 200, tracker.text
    cree = await client.post(
        f"/api/v1/projects/{project['id']}/work-items", json={"title": "Arrivée de Camille", "start": False}
    )
    assert cree.status_code == 201, cree.text
    return dict(cree.json())


async def _cible(client: AsyncClient, project: dict[str, Any]) -> str:
    publiee = await client.put(
        f"/api/v1/projects/{project['id']}/workflows/onboarding", json={"yaml": ARRIVEE}
    )
    assert publiee.status_code == 200, publiee.text
    return str(publiee.json()["id"])


async def _migrer(client: AsyncClient, ticket: dict[str, Any], **corps: Any) -> Any:
    return await client.post(
        f"/api/v1/work-items/{ticket['id']}/actions", json={"action": "migrate", **corps}
    )


def _signaux() -> list[tuple[str, str, Any]]:
    from choregos_api.temporal import get_temporal

    return list(get_temporal().signals)  # type: ignore[attr-defined]


async def test_la_definition_part_avec_le_signal(client: AsyncClient, project: dict[str, Any]) -> None:
    ticket, cible = await _ticket(client, project), await _cible(client, project)
    reponse = await _migrer(client, ticket, workflow_def_id=cible, state_mapping={ticket["state"]: "demande"})
    assert reponse.status_code == 202, reponse.text
    (_, nom, controle), *_ = [s for s in _signaux() if s[1] == "control"]
    assert nom == "control"
    assert controle["workflow"]["metadata"] == {"name": "onboarding", "version": 1}
    assert controle["workflow_def_id"] == cible
    assert controle["state_mapping"] == {ticket["state"]: "demande"}


async def test_une_migration_impossible_ne_part_pas(client: AsyncClient, project: dict[str, Any]) -> None:
    ticket, cible = await _ticket(client, project), await _cible(client, project)
    sans_mapping = await _migrer(client, ticket, workflow_def_id=cible)
    assert sans_mapping.status_code == 422, sans_mapping.text
    assert ticket["state"] in sans_mapping.text, "le refus nomme l'état qui manque"
    sans_cible = await _migrer(client, ticket, state_mapping={ticket["state"]: "demande"})
    assert sans_cible.status_code == 422, sans_cible.text
    inconnue = await _migrer(client, ticket, workflow_def_id="pas-une-definition")
    assert inconnue.status_code == 404, inconnue.text
    assert not [s for s in _signaux() if s[1] == "control"], "rien n'est transmis"


async def test_la_definition_d_un_autre_projet_est_introuvable(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    autre = await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": "rh", "name": "RH", "config": {"slug": "rh", "org": "varga"}},
    )
    assert autre.status_code == 201, autre.text
    etrangere = await _cible(client, autre.json())
    ticket = await _ticket(client, project)
    reponse = await _migrer(
        client, ticket, workflow_def_id=etrangere, state_mapping={ticket["state"]: "demande"}
    )
    assert reponse.status_code == 404, reponse.text


async def test_un_ticket_occupe_ne_migre_pas(client: AsyncClient, project: dict[str, Any]) -> None:
    """Une décision attendue, un run en cours : l'état bougerait avant la migration, et le mapping
    vérifié ici ne vaudrait plus."""
    from choregos_api.db.models import HumanRequest, Run
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow
    from sqlalchemy import select

    ticket, cible = await _ticket(client, project), await _cible(client, project)
    mapping = {ticket["state"]: "demande"}
    async with session_scope() as session:
        demande = HumanRequest(
            project_id=project["id"], work_item_id=ticket["id"], kind="approval", requested_at=utcnow()
        )
        session.add(demande)
    attend = await _migrer(client, ticket, workflow_def_id=cible, state_mapping=mapping)
    assert attend.status_code == 409 and "décision" in attend.text, attend.text

    async with session_scope() as session:
        lue = await session.get(HumanRequest, demande.id)
        assert lue is not None
        lue.decided_at = utcnow()
        session.add(
            Run(project_id=project["id"], work_item_id=ticket["id"], status="running", stage_role="build")
        )
    tourne = await _migrer(client, ticket, workflow_def_id=cible, state_mapping=mapping)
    assert tourne.status_code == 409 and "run" in tourne.text, tourne.text

    async with session_scope() as session:
        for run in (await session.execute(select(Run))).scalars():
            run.status = "succeeded"
    libre = await _migrer(client, ticket, workflow_def_id=cible, state_mapping=mapping)
    assert libre.status_code == 202, libre.text
