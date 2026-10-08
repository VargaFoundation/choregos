"""Le gabarit `joiners-leavers` (S20-07) : un projet RH naît avec ses deux workflows, leur défaut et
leur routage, et l'organisation reçoit ses deux agents et ses deux skills — en version 1 quand ils
n'y sont pas, jamais réécrits quand ils y sont. Les effets du cœur que ses workflows appellent :
`connector.call`, dont un serveur MCP rend ce qu'il a structuré, et `verifier`, un contrôle.
"""

from __future__ import annotations

import pathlib
import shutil
from typing import Any

import pytest
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"
GABARIT = pathlib.Path(__file__).resolve().parents[3] / "templates" / "joiners-leavers"


async def _projet(client: AsyncClient, slug: str = "rh", gabarit: str = "joiners-leavers") -> dict[str, Any]:
    cree = await client.post(
        f"{ORG}/projects",
        json={"slug": slug, "name": slug, "template_ref": gabarit, "config": {"slug": slug, "org": "varga"}},
    )
    assert cree.status_code == 201, cree.text
    return dict(cree.json())


async def test_un_projet_rh_nait_avec_ses_workflows_et_l_organisation_recoit_ses_agents_et_ses_skills(
    client: AsyncClient, admin: str
) -> None:
    pid = (await _projet(client))["id"]
    assert {w["name"] for w in (await client.get(f"/api/v1/projects/{pid}/workflows")).json()} == {
        "onboarding",
        "offboarding",
    }
    routage = (await client.get(f"/api/v1/projects/{pid}/workflow-routing")).json()
    assert routage["default"] == "onboarding"
    assert routage["rules"][0]["when"]["labels_any"] == ["offboarding", "depart"]
    agents = {a["slug"]: a for a in (await client.get(f"{ORG}/agents")).json()}
    assert {"coordinateur-onboarding", "coordinateur-offboarding"} <= set(agents)
    detail = (await client.get(f"{ORG}/agents/coordinateur-onboarding")).json()
    (version,) = detail["versions"]
    assert version["created_by"] == "template:joiners-leavers"
    assert [s["slug"] for s in version["spec"]["skills"]] == ["procedure-onboarding", "profils-d-acces"]
    skills = {s["slug"]: s for s in (await client.get(f"{ORG}/skills")).json()}
    assert skills["profils-d-acces"]["latest_version"] == 1
    assert skills["procedure-onboarding"]["latest_version"] == 1


async def test_un_second_projet_ne_reecrit_pas_ce_que_l_organisation_a_fait_evoluer(
    client: AsyncClient, admin: str
) -> None:
    await _projet(client)
    lue = (await client.get(f"{ORG}/skills/profils-d-acces/versions/1")).json()
    fichiers = {
        **lue["files"],
        "SKILL.md": lue["files"]["SKILL.md"] + "\n| Juriste | `juridique` | `contentieux` |\n",
    }
    publiee = await client.post(f"{ORG}/skills/profils-d-acces/versions", json={"files": fichiers})
    assert publiee.status_code == 201, publiee.text
    await _projet(client, slug="rh-lyon")
    skills = {s["slug"]: s for s in (await client.get(f"{ORG}/skills")).json()}
    assert skills["profils-d-acces"]["latest_version"] == 2, "la version de l'organisation tient"
    detail = (await client.get(f"{ORG}/agents/coordinateur-onboarding")).json()
    assert len(detail["versions"]) == 1, "l'agent n'est pas réinstallé"


async def test_un_depart_nait_dans_son_workflow_et_ses_champs_sont_ceux_du_depart(
    client: AsyncClient, admin: str
) -> None:
    pid = (await _projet(client))["id"]
    tracker = await client.put(
        f"/api/v1/projects/{pid}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert tracker.status_code == 200, tracker.text
    champs = {
        "nom": "Paul Martin",
        "upn": "paul@acme.test",
        "date_depart": "2026-11-30",
        "badge_uid": "04A1B2C3",
        "serial": "PC-0042",
        "adresse": "12 rue de la Paix, Paris",
    }
    depart = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "Départ de Paul", "labels": ["depart"], "fields": champs, "start": False},
    )
    assert depart.status_code == 201, depart.text
    assert depart.json()["state"] == "demande" and depart.json()["workflow_name"] == "offboarding"
    incomplet = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={"title": "Départ", "labels": ["depart"], "fields": {"nom": "x"}, "start": False},
    )
    assert incomplet.status_code == 422 and "upn" in incomplet.text


async def test_une_skill_du_gabarit_ne_lit_rien_hors_d_elle(
    client: AsyncClient, admin: str, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    copie = tmp_path / "joiners-leavers"
    shutil.copytree(GABARIT, copie)
    (copie / "skills" / "profils-d-acces" / "fuite.md").symlink_to("/etc/hostname")
    monkeypatch.setenv("CHOREGOS_TEMPLATES_DIR", str(tmp_path))
    refus = await client.post(
        f"{ORG}/projects",
        json={
            "slug": "rh",
            "name": "rh",
            "template_ref": "joiners-leavers",
            "config": {"slug": "rh", "org": "varga"},
        },
    )
    assert refus.status_code == 422 and "symbolic link" in refus.text


async def test_verifier_tient_ou_refuse_et_un_serveur_mcp_rend_ce_qu_il_a_structure(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    import choregos_adapters
    from choregos_adapters.fakes.rh import FakeFournisseur, serveur_mcp
    from choregos_api.db.models import Action, ConnectorOperation, OrgConnector, Project
    from choregos_api.db.session import session_scope
    from choregos_api.effets import ContexteEffet, EffetRefuse, effet, politique_de_l_effet

    assert politique_de_l_effet("verifier") == "allowed", "un contrôle ne touche rien au-dehors"
    monkeypatch.setattr(choregos_adapters, "FAUX_MCP", serveur_mcp(FakeFournisseur()))
    async with session_scope() as session:
        projet = await session.get(Project, project["id"])
        assert projet is not None
        fournisseur = OrgConnector(org_id=projet.org_id, name="fournisseur", kind="mcp", type="mcp",
                                   config={"url": "https://fournisseur.test/mcp"})  # fmt: skip
        session.add(fournisseur)
        await session.flush()
        session.add(
            ConnectorOperation(
                org_id=projet.org_id,
                connector_id=fournisseur.id,
                name="commander_poste",
                access="write",
                policy="approval",
                groups=[],
            )
        )
        action = Action(
            org_id=projet.org_id,
            project_id=projet.id,
            origin="transition",
            kind="x",
            title="x",
            params={},
            effects=[],
            proposed_by={},
            approval={},
            decisions=[],
            status="approved",
        )
        session.add(action)
        await session.flush()
        ctx = ContexteEffet(session, action, projet)
        commande = await effet("connector.call")(
            ctx,
            {"connector": "fournisseur", "operation": "commander_poste",
             "arguments": {"reference": "RH-1", "modele": "p14", "upn": "lea"}},
        )  # fmt: skip
        assert commande["serial"].startswith("PC-"), "l'objet structuré, pas l'enveloppe du protocole"
        assert (await effet("verifier")(ctx, {"condition": "True", "motif": "tout tient"}))["ok"] is True
        with pytest.raises(EffetRefuse, match="check failed: le badge actif"):
            await effet("verifier")(ctx, {"condition": "False", "motif": "le badge actif"})
