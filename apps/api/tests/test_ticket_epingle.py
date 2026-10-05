"""Un ticket est épinglé à la version du workflow où il est né, et l'écran le dit (ADR 0031).

La vue d'un ticket retombait sur le gabarit `default-simple` faute d'épingle : un projet qui n'avait
jamais eu ce workflow l'affichait pour chacun de ses tickets. Et publier un autre workflow changeait
rétroactivement celui de tickets déjà en cours.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

STAFFING = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: staffing, version: 1}
actors:
  rh: {type: human, group: rh}
states:
  demande: {display: Demande, kind: wait}
  pourvu: {display: Pourvu, terminal: true}
transitions:
  - {id: t-pourvoir, from: demande, to: pourvu, by: rh}
"""


async def test_le_ticket_garde_son_workflow_quand_le_defaut_change(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    publie = await client.put(f"/api/v1/projects/{pid}/workflow", json={"yaml": STAFFING})
    assert publie.status_code == 200, publie.text
    cree = await client.post(f"/api/v1/projects/{pid}/work-items", json={"title": "Un poste", "start": False})
    assert cree.status_code == 201, cree.text
    ticket = cree.json()
    assert (ticket["workflow_name"], ticket["state"]) == ("staffing", "demande")

    # Le défaut redevient default-simple : le ticket, lui, reste dans staffing.
    from choregos_core import template_yaml

    autre = await client.put(
        f"/api/v1/projects/{pid}/workflow", json={"yaml": template_yaml("default-simple")}
    )
    assert autre.status_code == 200, autre.text
    relu = (await client.get(f"/api/v1/work-items/{ticket['id']}")).json()
    assert (relu["workflow_name"], relu["workflow_version"]) == ("staffing", 1)
