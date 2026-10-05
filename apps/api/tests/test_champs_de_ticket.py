"""Les champs d'un ticket, déclarés par son workflow (ADR 0031).

Un ticket n'avait qu'un titre, un corps, une taille et un risque : un onboarding n'avait nulle part
où dire la date d'arrivée ni le poste, et un agent devait les deviner dans un texte libre.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

ONBOARDING = """apiVersion: choregos/v1
kind: Workflow
metadata:
  name: onboarding
  version: 1
  inputs:
    type: object
    required: [date_arrivee, poste]
    additionalProperties: false
    properties:
      date_arrivee: {type: string, format: date}
      poste: {type: string, minLength: 1}
actors:
  rh: {type: human, group: rh}
states:
  arrivee: {display: Arrivée, kind: wait}
  fait: {display: Fait, terminal: true}
transitions:
  - {id: t-accueillir, from: arrivee, to: fait, by: rh}
"""


async def _projet_rh(client: AsyncClient, project: dict[str, Any]) -> str:
    pid = project["id"]
    reponse = await client.put(f"/api/v1/projects/{pid}/workflows/onboarding", json={"yaml": ONBOARDING})
    assert reponse.status_code == 200, reponse.text
    return str(pid)


async def test_les_champs_sont_valides_a_la_naissance(client: AsyncClient, project: dict[str, Any]) -> None:
    pid = await _projet_rh(client, project)
    corps = {"title": "Arrivée de Jeanne", "start": False, "workflow": "onboarding"}
    manquant = await client.post(
        f"/api/v1/projects/{pid}/work-items", json={**corps, "fields": {"poste": "data"}}
    )
    assert manquant.status_code == 422, manquant.text
    etranger = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={**corps, "fields": {"date_arrivee": "2026-11-03", "poste": "data", "salaire": 1}},
    )
    assert etranger.status_code == 422, etranger.text
    bon = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={**corps, "fields": {"date_arrivee": "2026-11-03", "poste": "consultante data"}},
    )
    assert bon.status_code == 201, bon.text
    assert bon.json()["fields"] == {"date_arrivee": "2026-11-03", "poste": "consultante data"}


async def test_un_workflow_sans_champs_refuse_des_champs(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    reponse = await client.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={"title": "x", "start": False, "fields": {"a": 1}},
    )
    assert reponse.status_code == 422, reponse.text


async def test_un_schema_de_champs_invalide_est_refuse_a_la_publication(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    faux = ONBOARDING.replace("type: object\n    required", "type: array\n    required")
    reponse = await client.put(f"/api/v1/projects/{project['id']}/workflows/onboarding", json={"yaml": faux})
    assert reponse.status_code == 422, reponse.text


async def test_les_champs_arrivent_dans_le_contexte_du_run(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """L'agent lit le ticket par `get_ticket` : les champs y sont, il n'a plus à les deviner."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    pid = await _projet_rh(client, project)
    champs = {"date_arrivee": "2026-11-03", "poste": "consultante data"}
    cree = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "Arrivée de Jeanne", "start": False, "workflow": "onboarding", "fields": champs},
    )
    ticket = cree.json()
    async with session_scope() as session:
        session.add(
            Run(
                id="run-champs",
                work_item_id=ticket["id"],
                project_id=pid,
                stage_role="custom",
                status="running",
            )
        )
    jeton = mint_run_token(
        "run-champs", project_slug="billing-api", work_item_key=ticket["tracker_key"], ttl_minutes=5
    )
    lu = await client.get(
        "/api/v1/internal/runs/run-champs/ticket", headers={"Authorization": f"Bearer {jeton}"}
    )
    assert lu.status_code == 200, lu.text
    assert lu.json()["fields"] == champs
