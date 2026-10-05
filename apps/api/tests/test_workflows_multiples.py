"""Un projet porte plusieurs workflows, un actif par nom (ADR 0031).

Un projet RH doit tenir l'arrivée et le départ côte à côte. Avant, publier `offboarding`
désactivait `onboarding`, et « le workflow du projet » était la version active la plus haute,
quel que soit son nom.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient


def _flux(nom: str, etat: str = "demande", version: int = 1) -> str:
    return f"""apiVersion: choregos/v1
kind: Workflow
metadata: {{name: {nom}, version: {version}}}
actors:
  rh: {{type: human, group: rh}}
states:
  {etat}: {{display: Demande, kind: wait}}
  fait: {{display: Fait, terminal: true}}
transitions:
  - {{id: t-faire, from: {etat}, to: fait, by: rh}}
"""


async def _publier(client: AsyncClient, pid: str, nom: str, **extra: Any) -> Any:
    return await client.put(f"/api/v1/projects/{pid}/workflows/{nom}", json={"yaml": _flux(nom), **extra})


async def test_arrivee_et_depart_actifs_ensemble(client: AsyncClient, project: dict[str, Any]) -> None:
    pid = project["id"]
    for nom in ("onboarding", "offboarding"):
        reponse = await _publier(client, pid, nom)
        assert reponse.status_code == 200, reponse.text
    liste = (await client.get(f"/api/v1/projects/{pid}/workflows")).json()
    noms = {w["name"] for w in liste}
    assert {"onboarding", "offboarding"} <= noms, liste
    # Le défaut n'a pas bougé : publier par nom n'en fait pas le défaut.
    defaut = (await client.get(f"/api/v1/projects/{pid}/workflow")).json()
    assert defaut["name"] == "default-simple" and defaut["is_default"] is True


async def test_le_nom_de_la_route_et_celui_du_yaml_coincident(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    reponse = await client.put(
        f"/api/v1/projects/{project['id']}/workflows/onboarding", json={"yaml": _flux("offboarding")}
    )
    assert reponse.status_code == 422, reponse.text


async def test_une_version_lue_perimee_recoit_409(client: AsyncClient, project: dict[str, Any]) -> None:
    pid = project["id"]
    premiere = (await _publier(client, pid, "onboarding")).json()
    assert (await _publier(client, pid, "onboarding", base_version=premiere["version"])).status_code == 200
    perimee = await _publier(client, pid, "onboarding", base_version=premiere["version"])
    assert perimee.status_code == 409, perimee.text


async def test_restaurer_cree_la_version_suivante(client: AsyncClient, project: dict[str, Any]) -> None:
    pid = project["id"]
    v1 = (await _publier(client, pid, "onboarding")).json()
    v2 = (await _publier(client, pid, "onboarding")).json()
    restauree = await client.post(
        f"/api/v1/projects/{pid}/workflows/onboarding/versions/{v1['version']}/restore"
    )
    assert restauree.status_code == 200, restauree.text
    assert restauree.json()["version"] == v2["version"] + 1
    versions = (await client.get(f"/api/v1/projects/{pid}/workflows/onboarding/versions")).json()
    assert [v["version"] for v in versions] == [v2["version"] + 1, v2["version"], v1["version"]]
    assert [v["is_active"] for v in versions] == [True, False, False]


async def test_le_defaut_et_une_cible_de_routage_ne_se_desactivent_pas(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    for nom in ("onboarding", "offboarding", "mobilite"):
        assert (await _publier(client, pid, nom)).status_code == 200
    routage = {
        "default": "onboarding",
        "rules": [{"when": {"labels_any": ["depart"]}, "workflow": "offboarding"}],
    }
    pose = await client.put(f"/api/v1/projects/{pid}/workflow-routing", json=routage)
    assert pose.status_code == 200, pose.text
    assert (await client.post(f"/api/v1/projects/{pid}/workflows/onboarding/deactivate")).status_code == 409
    assert (await client.post(f"/api/v1/projects/{pid}/workflows/offboarding/deactivate")).status_code == 409
    assert (await client.post(f"/api/v1/projects/{pid}/workflows/mobilite/deactivate")).status_code == 204
    noms = {w["name"] for w in (await client.get(f"/api/v1/projects/{pid}/workflows")).json()}
    assert "mobilite" not in noms
    assert (await client.get(f"/api/v1/projects/{pid}/workflow")).json()["name"] == "onboarding"


async def test_un_routage_vers_un_workflow_inconnu_est_refuse(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    reponse = await client.put(
        f"/api/v1/projects/{project['id']}/workflow-routing",
        json={"default": "default-simple", "rules": [{"when": {"item_type": "Bug"}, "workflow": "fantome"}]},
    )
    assert reponse.status_code == 422, reponse.text


# ───────────────────────────── la naissance d'un ticket (S16-03) ─────────────────────────────


async def _deux_flux(client: AsyncClient, pid: str) -> None:
    for nom, etat in (("onboarding", "arrivee"), ("offboarding", "depart")):
        reponse = await client.put(f"/api/v1/projects/{pid}/workflows/{nom}", json={"yaml": _flux(nom, etat)})
        assert reponse.status_code == 200, reponse.text


async def _naitre(client: AsyncClient, pid: str, **champs: Any) -> Any:
    return await client.post(
        f"/api/v1/projects/{pid}/work-items", json={"title": "x", "start": False, **champs}
    )


async def test_un_ticket_nait_dans_le_workflow_demande(client: AsyncClient, project: dict[str, Any]) -> None:
    pid = project["id"]
    await _deux_flux(client, pid)
    ticket = (await _naitre(client, pid, workflow="offboarding")).json()
    assert (ticket["workflow_name"], ticket["state"]) == ("offboarding", "depart")


async def test_le_routage_choisit_par_etiquette_et_le_defaut_sinon(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    pid = project["id"]
    await _deux_flux(client, pid)
    routage = {
        "default": "onboarding",
        "rules": [{"when": {"labels_any": ["depart"]}, "workflow": "offboarding"}],
    }
    assert (await client.put(f"/api/v1/projects/{pid}/workflow-routing", json=routage)).status_code == 200
    route = (await _naitre(client, pid, labels=["rh", "depart"])).json()
    assert (route["workflow_name"], route["state"]) == ("offboarding", "depart")
    defaut = (await _naitre(client, pid, labels=["rh"])).json()
    assert (defaut["workflow_name"], defaut["state"]) == ("onboarding", "arrivee")


async def test_un_workflow_demande_inconnu_est_refuse(client: AsyncClient, project: dict[str, Any]) -> None:
    reponse = await _naitre(client, project["id"], workflow="fantome")
    assert reponse.status_code == 422, reponse.text
