# SPDX-License-Identifier: Apache-2.0
"""Le gabarit `joiners-leavers` livre son ontologie (S20-09) : quand le greffon est actif, un projet RH
naît avec le registre de ce qu'une arrivée ouvre et de ce qu'un départ ferme.

Le registre se tient par des actions de l'ontologie, jouées par l'`ActionWorkflow` comme toute action
du cœur : consigner ce qu'un workflow vient d'ouvrir est d'un risque faible, la politique décide ;
constater un départ ferme des accès au registre, une personne ré-authentifiée en décide.

Ce que ces tests ne prouvent pas : que les workflows du gabarit écrivent eux-mêmes au registre — ils
agissent sur l'annuaire, le parc et les lecteurs, et rien ne les y oblige encore (la réconciliation
quotidienne attend).
"""

from __future__ import annotations

import asyncio
from typing import Any

from httpx import AsyncClient

from .aides import objets

ARRIVEE = {
    "matricule": "M-1042",
    "nom": "Léa Martin",
    "email": "lea.martin@acme.test",
    "poste": "Comptable",
    "manager": "paul.durand@acme.test",
    "date_arrivee": "2026-11-02",
    "contrat": "C-2026-118",
    "nature": "cdi",
}


async def _projet_rh(client: AsyncClient) -> dict[str, Any]:
    cree = await client.post(
        "/api/v1/orgs/varga/projects",
        json={
            "slug": "rh",
            "name": "RH",
            "template_ref": "joiners-leavers",
            "config": {"slug": "rh", "org": "varga"},
        },
    )
    assert cree.status_code == 201, cree.text
    return dict(cree.json())


async def _proposer(client: AsyncClient, projet: dict[str, Any], action: str, **corps: Any) -> dict[str, Any]:
    reponse = await client.post(
        f"/api/v1/projects/{projet['id']}/proposals",
        json={"action_type": action, "justification": "la demande validée par les RH", **corps},
    )
    assert reponse.status_code == 201, reponse.text
    return dict(reponse.json())


async def _jusqu_a(
    client: AsyncClient, projet: dict[str, Any], proposition: str, *statuts: str
) -> dict[str, Any]:
    for _ in range(400):
        dossier = (await client.get(f"/api/v1/projects/{projet['id']}/proposals/{proposition}")).json()
        if dossier["status"] in statuts:
            return dict(dossier)
        await asyncio.sleep(0.05)
    raise AssertionError(f"{proposition} : {dossier['status']}, jamais {statuts} ({dossier.get('error')})")


async def test_un_projet_rh_nait_avec_son_registre(client: AsyncClient) -> None:
    projet = await _projet_rh(client)
    ontologie = (await client.get(f"/api/v1/projects/{projet['id']}/ontology")).json()
    assert (ontologie["name"], ontologie["version"]) == ("rh-arrivees-departs", "1.0.0")
    assert set(ontologie["object_types"]) == {
        "collaborateur",
        "contrat",
        "account",
        "group",
        "materiel",
        "badge",
    }
    assert {"collaborateur_search", "badge_porteur", "action_constater_un_depart"} <= set(
        ontologie["mcp_tools"]
    )
    audit = (await client.get("/api/v1/audit")).json()["items"]
    (ligne,) = [a for a in audit if a["action"] == "template.extension.install"]
    assert ligne["payload"]["extension"] == "ontology"
    assert ligne["payload"]["result"]["name"] == "rh-arrivees-departs"
    assert not [a for a in audit if a["action"] == "template.extension.skip"]


async def test_le_registre_se_tient_par_des_actions_et_un_depart_attend_une_personne(
    client: AsyncClient, temporal: Any
) -> None:
    projet = await _projet_rh(client)
    arrivee = await _proposer(client, projet, "consigner_une_arrivee", params=ARRIVEE)
    dossier = await _jusqu_a(client, projet, arrivee["proposal"], "succeeded", "failed")
    assert dossier["status"] == "succeeded", dossier.get("error")
    (_, lea), *_ = (await objets(projet["id"], "collaborateur")).values()
    assert (lea["nom"], lea["statut"]) == ("Léa Martin", "a_venir")
    assert (await objets(projet["id"], "contrat"))["C-2026-118"][1]["nature"] == "cdi"

    badge = await _proposer(
        client, projet, "consigner_le_badge", target=["M-1042"], params={"uid": "04:A2:19:7F"}
    )
    dossier = await _jusqu_a(client, projet, badge["proposal"], "succeeded", "failed")
    assert dossier["status"] == "succeeded", dossier.get("error")
    assert (await objets(projet["id"], "badge"))["04:A2:19:7F"][1]["actif"] is True
    assert (await objets(projet["id"], "collaborateur"))["M-1042"][1]["statut"] == "present"

    depart = await _proposer(
        client,
        projet,
        "constater_un_depart",
        target=["M-1042"],
        params={
            "date_depart": "2027-03-31",
            "upn": "lea.martin@acme.test",
            "badge": "04:A2:19:7F",
            "numero_de_serie": "PF3K9Z",
        },
    )
    assert depart["status"] == "pending_approval", "un départ attend une personne"
    assert (await objets(projet["id"], "badge"))["04:A2:19:7F"][1]["actif"] is True, "rien n'est fermé avant"


async def test_rien_ne_se_consigne_pour_une_personne_partie(client: AsyncClient, temporal: Any) -> None:
    from choregos_api.db.session import session_scope
    from choregos_ontology.service.store import ManagedObject

    projet = await _projet_rh(client)
    arrivee = await _proposer(client, projet, "consigner_une_arrivee", params=ARRIVEE)
    assert (await _jusqu_a(client, projet, arrivee["proposal"], "succeeded", "failed"))[
        "status"
    ] == "succeeded"
    # Le départ, déjà constaté : écrit ici directement — la décision d'une personne a son propre test.
    async with session_scope() as session:
        lea = await session.get(ManagedObject, (projet["id"], "collaborateur", "M-1042"))
        assert lea is not None
        lea.properties = {**lea.properties, "statut": "parti"}
    refus = await client.post(
        f"/api/v1/projects/{projet['id']}/proposals",
        json={
            "action_type": "consigner_le_badge",
            "target": ["M-1042"],
            "params": {"uid": "04:FF:00:01"},
            "justification": "un badge pour une personne partie",
        },
    )
    assert refus.status_code == 422, refus.text
    assert "pas_parti" in refus.text
    assert "04:FF:00:01" not in await objets(projet["id"], "badge")
