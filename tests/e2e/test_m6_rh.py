"""M6 — le scénario RH de bout en bout (S20-07), sur des faux, en temps accéléré.

Arrivée : le Claude d'une RH dépose la demande par la porte MCP ; l'agent du registre prépare le
plan d'accès ; une RH le valide ; à J-10 le compte et son groupe naissent dans l'annuaire — la
politique de l'organisation a décidé ; le groupe sensible attend une personne ré-authentifiée ; à
J-7 le poste est commandé à l'agent du fournisseur (MCP, sous validation), inscrit au parc sous le
numéro qu'il a attribué et expédié ; l'accueil remet le badge — une tâche, attestée — et la
plateforme l'active ; à J+1 elle vérifie que tout tient.

Départ : à J0 le compte, ses sessions et le badge sont coupés d'un geste ; le poste est repris,
reçu — une tâche — et effacé ; la plateforme vérifie.

Chaque brique est éprouvée sur son faux : en retirer une fait rougir le scénario.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from choregos_contracts import StageResult, StageStatus
from httpx import AsyncClient

from .conftest import Platform, login

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

ORG = "/api/v1/orgs/varga"
CLE_ANNUAIRE = "secret-de-l-application-entra"
CLE_PARC = "cle-du-parc"
CLE_FOURNISSEUR = "jeton-du-fournisseur"
ATTESTATION_BADGE = "I handed the badge to its holder in person"
ATTESTATION_RECEPTION = "I received this laptop and checked its serial number"


class Faux:
    """Les systèmes que le scénario RH touche, chacun avec la clé qu'il attend."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import choregos_adapters
        from choregos_adapters.fakes.entra import FakeEntra
        from choregos_adapters.fakes.rh import (
            FakeBadges,
            FakeFournisseur,
            FakeMdm,
            FakeTransporteur,
            serveur_mcp,
        )

        self.annuaire = FakeEntra(secret_attendu=CLE_ANNUAIRE)
        self.parc = FakeMdm(cle_attendue=CLE_PARC)
        self.transporteur = FakeTransporteur(cle_attendue=CLE_PARC)
        self.lecteurs = FakeBadges(cle_attendue=CLE_PARC)
        self.fournisseur = FakeFournisseur()
        self.serveur = serveur_mcp(self.fournisseur, jeton=CLE_FOURNISSEUR)
        monkeypatch.setattr(choregos_adapters, "FAUX_ENTRA", self.annuaire)
        monkeypatch.setattr(choregos_adapters, "FAUX_MCP", self.serveur)
        for famille, faux in (
            ("mdm", self.parc),
            ("shipping", self.transporteur),
            ("access_control", self.lecteurs),
        ):
            monkeypatch.setitem(choregos_adapters.FAUX_METIER, famille, faux)
        monkeypatch.setenv("E2E_CLE_ANNUAIRE", CLE_ANNUAIRE)
        monkeypatch.setenv("E2E_CLE_PARC", CLE_PARC)
        monkeypatch.setenv("E2E_CLE_FOURNISSEUR", CLE_FOURNISSEUR)


@pytest.fixture
def faux(monkeypatch: pytest.MonkeyPatch) -> Faux:
    return Faux(monkeypatch)


async def _organisation(client: AsyncClient) -> None:
    """Le geste de l'administrateur : les cinq connecteurs que le gabarit annonce, leurs clés en
    référence, et ce que l'organisation permet sans décision humaine."""
    for corps in (
        {"name": "annuaire", "kind": "identity", "type": "entra",
         "config": {"tenant_id": "acme", "client_id": "choregos"},
         "secret_refs": {"client_secret": "env:E2E_CLE_ANNUAIRE"}},
        {"name": "parc", "kind": "mdm", "type": "demo", "secret_refs": {"api_key": "env:E2E_CLE_PARC"}},
        {"name": "transporteur", "kind": "shipping", "type": "demo",
         "secret_refs": {"api_key": "env:E2E_CLE_PARC"}},
        {"name": "lecteurs", "kind": "access_control", "type": "demo",
         "secret_refs": {"api_key": "env:E2E_CLE_PARC"}},
        {"name": "fournisseur", "kind": "mcp", "type": "mcp", "config": {"url": "https://fournisseur.test/mcp"},
         "secret_refs": {"token": "env:E2E_CLE_FOURNISSEUR"}},
    ):  # fmt: skip
        cree = await client.post(f"{ORG}/connectors", json=corps)
        assert cree.status_code == 201, cree.text
    # Les outils du fournisseur se DÉCOUVRENT, et naissent fermés.
    decouverte = await client.post(f"{ORG}/connectors/fournisseur/discover")
    assert decouverte.status_code == 200, decouverte.text
    operations = {
        o["name"]: o["policy"]
        for o in (await client.get(f"{ORG}/connectors/fournisseur")).json()["operations"]
    }
    assert operations == {"commander_poste": "forbidden", "suivi_commande": "forbidden"}, (
        "un outil découvert naît fermé"
    )
    permises = {
        "annuaire": ["create_user", "add_to_group", "remove_from_group", "disable_user", "revoke_sessions"],
        "parc": ["enroll_device", "wipe_device"],
        "transporteur": ["create_shipment", "create_return"],
        "lecteurs": ["activate_badge", "deactivate_badge"],
    }
    for connecteur, noms in permises.items():
        for nom in noms:
            ouverte = await client.patch(
                f"{ORG}/connectors/{connecteur}/operations/{nom}", json={"policy": "allowed"}
            )
            assert ouverte.status_code == 200, ouverte.text
    # Un achat se décide : la commande du poste reste sous validation.
    sous_validation = await client.patch(
        f"{ORG}/connectors/fournisseur/operations/commander_poste", json={"policy": "approval"}
    )
    assert sous_validation.status_code == 200, sous_validation.text


async def _projet_rh(client: AsyncClient) -> dict[str, Any]:
    cree = await client.post(
        f"{ORG}/projects",
        json={
            "slug": "rh",
            "name": "RH",
            "template_ref": "joiners-leavers",
            "config": {"slug": "rh", "org": "varga"},
        },
    )
    assert cree.status_code == 201, cree.text
    projet = dict(cree.json())
    tracker = await client.put(
        f"/api/v1/projects/{projet['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert tracker.status_code == 200, tracker.text
    return projet


async def _par_claude(platform: Platform, client: AsyncClient, champs: dict[str, Any]) -> dict[str, Any]:
    """La demande, déposée par le Claude d'une RH : un jeton `mcp:write`, la porte du projet."""
    jeton = (
        await client.post("/api/v1/me/tokens", json={"name": "claude-de-la-rh", "scopes": ["mcp:write"]})
    ).json()
    claude = AsyncClient(transport=client._transport, base_url="http://e2e")
    try:
        appel = await claude.post(
            "/mcp/projects/varga:rh",
            headers={"Authorization": f"Bearer {jeton['token']}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                  "params": {"name": "create_work_item",
                             "arguments": {"title": f"Arrivée de {champs['nom']}", "workflow": "onboarding",
                                           "fields": champs}}},
        )  # fmt: skip
    finally:
        await claude.aclose()
    assert appel.status_code == 200, appel.text
    resultat = appel.json()["result"]
    assert resultat["isError"] is False, resultat
    return dict(resultat["structuredContent"])


async def _demarrer(platform: Platform, projet: dict[str, Any], ticket: dict[str, Any]) -> Any:
    """Le scénario démarre l'interpréteur sur la file des workers, sous l'identifiant de l'API."""
    from choregos_api.temporal import interpreter_id
    from choregos_core.domain import WorkItemData

    platform.adapters.tracker.items.setdefault(
        ticket["key"], WorkItemData(key=ticket["key"], title="RH", state="Todo")
    )
    return await platform.env.client.start_workflow(
        "WorkflowInterpreter",
        {"project_id": projet["id"], "project_slug": "rh", "work_item_id": ticket["work_item"],
         "tracker_key": ticket["key"]},
        id=interpreter_id("rh", ticket["key"]),
        task_queue="e2e",
    )  # fmt: skip


async def _etat(client: AsyncClient, ticket_id: str, attendu: str, quoi: str) -> dict[str, Any]:
    for _ in range(400):
        lu = (await client.get(f"/api/v1/work-items/{ticket_id}")).json()
        if lu["state"] == attendu:
            return dict(lu)
        await asyncio.sleep(0.05)
    raise AssertionError(f"{quoi} : le ticket est en `{lu['state']}`, pas en `{attendu}`")


async def _demande(client: AsyncClient, ticket_id: str) -> dict[str, Any]:
    """La demande humaine que l'interpréteur vient d'ouvrir sur le ticket."""
    for _ in range(400):
        lu = (await client.get(f"/api/v1/work-items/{ticket_id}")).json()
        if lu.get("pending_request"):
            return dict(lu["pending_request"])
        await asyncio.sleep(0.05)
    raise AssertionError("aucune demande humaine ouverte")


async def _attendre(handle: Any, predicat: Any, quoi: str) -> dict[str, Any]:
    for _ in range(400):
        statut = await handle.query("status")
        if predicat(statut):
            return dict(statut)
        await asyncio.sleep(0.05)
    raise AssertionError(f"{quoi} : jamais vrai ({await handle.query('status')})")


async def _approuver_l_action(client: AsyncClient, projet: dict[str, Any], genre: str) -> dict[str, Any]:
    """L'action en attente de ce genre, approuvée par une personne ré-authentifiée."""
    for _ in range(400):
        attente = (
            await client.get(
                f"/api/v1/projects/{projet['id']}/actions", params={"status": "pending_approval"}
            )
        ).json()
        trouvees = [a for a in attente if a["kind"] == genre]
        if trouvees:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError(f"aucune action `{genre}` en attente")
    decidee = await client.post(
        f"/api/v1/projects/{projet['id']}/actions/{trouvees[0]['id']}/decision", json={"decision": "approve"}
    )
    assert decidee.status_code == 200 and decidee.json()["status"] == "approved", decidee.text
    return dict(decidee.json())


async def _actions(client: AsyncClient, projet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["kind"]: a for a in (await client.get(f"/api/v1/projects/{projet['id']}/actions")).json()}


def _plan(nom: str) -> StageResult:
    return StageResult(
        status=StageStatus.DONE, summary="plan prêt", outputs={nom: f"## {nom}\n- rien d'anormal"}
    )


async def test_une_arrivee_de_la_demande_par_claude_au_controle_a_j_plus_1(
    platform: Platform, worker: Any, faux: Faux
) -> None:
    client = platform.client
    await login(client)
    await _organisation(client)
    projet = await _projet_rh(client)
    debut = await platform.env.get_current_time()
    arrivee = (debut + timedelta(days=15)).date()
    champs = {
        "nom": "Léa Martin",
        "upn": "lea@acme.test",
        "date_arrivee": arrivee.isoformat(),
        "poste": "Développeuse",
        "groupe": "devs",
        "groupe_sensible": "prod-lecture",
        "adresse": "12 rue de la Paix, Paris",
    }
    ticket = await _par_claude(platform, client, champs)

    async with worker():
        platform.adapters.executor.queue_result(_plan("plan_d_acces"))
        interprete = await _demarrer(platform, projet, ticket)
        await _etat(client, ticket["work_item"], "plan", "l'agent du registre a préparé le plan")
        await _demande(client, ticket["work_item"])
        assert (
            await client.post(f"/api/v1/work-items/{ticket['work_item']}/decisions", json={"kind": "approve"})
        ).status_code == 202

        # J-10 : la politique a permis le compte et son groupe — l'action naît approuvée et se joue.
        statut = await _attendre(interprete, lambda s: s.get("waiting_until"), "J-10 attendu")
        assert statut["waiting_until"].startswith((arrivee - timedelta(days=10)).isoformat())
        assert faux.annuaire.comptes == {}, "rien avant J-10"
        await platform.env.sleep(timedelta(days=5, hours=1))
        await _etat(client, ticket["work_item"], "comptes", "le compte est créé à J-10")
        (compte,) = faux.annuaire.comptes.values()
        assert (
            compte["userPrincipalName"] == "lea@acme.test"
            and compte["id"] in faux.annuaire.membres_de_l_unite
        )
        assert compte["id"] in faux.annuaire.groupes["devs"]

        # Le groupe sensible : une personne ré-authentifiée.
        await _approuver_l_action(client, projet, "arrivee.groupe_sensible")
        await _etat(client, ticket["work_item"], "sensible", "le groupe sensible est décidé")
        assert compte["id"] in faux.annuaire.groupes["prod-lecture"]

        # J-7 : la commande au fournisseur se décide ; le parc et l'envoi suivent son numéro de série.
        await _attendre(interprete, lambda s: s.get("waiting_until"), "J-7 attendu")
        assert faux.fournisseur.commandes == {}
        await platform.env.sleep(timedelta(days=3))
        await _approuver_l_action(client, projet, "arrivee.poste")
        await _etat(client, ticket["work_item"], "poste", "le poste est commandé, inscrit, expédié")
        (commande,) = faux.fournisseur.commandes.values()
        serie = commande["serial"]
        assert faux.parc.postes[serie] == {"serial": serie, "upn": "lea@acme.test", "state": "enrolled"}
        (envoi,) = faux.transporteur.envois.values()
        assert (envoi["items"], envoi["address"]) == ([serie], champs["adresse"])
        assert {e["authorization"] for _, e, _ in faux.serveur.recues} == {f"Bearer {CLE_FOURNISSEUR}"}

        # Le badge : une tâche, sa preuve ; puis la plateforme l'active.
        demande = await _demande(client, ticket["work_item"])
        assert (demande["kind"], demande["payload"]["attest"]) == ("task", ATTESTATION_BADGE)
        tache = await client.post(
            f"/api/v1/work-items/{ticket['work_item']}/decisions",
            json={"kind": "complete", "values": {"badge_uid": "04A1B2C3"}, "attested": True},
        )
        assert tache.status_code == 202, tache.text
        await _etat(client, ticket["work_item"], "actif", "le badge est actif")
        assert faux.lecteurs.badges["04A1B2C3"] == {
            "uid": "04A1B2C3",
            "holder": "lea@acme.test",
            "active": True,
        }

        # J+1 : la plateforme relit et vérifie.
        await platform.env.sleep(timedelta(days=12))
        resultat = await interprete.result()
    assert resultat["state"] == "pret"

    actions = await _actions(client, projet)
    assert {k: a["status"] for k, a in actions.items()} == {
        "arrivee.comptes": "succeeded",
        "arrivee.groupe_sensible": "succeeded",
        "arrivee.poste": "succeeded",
        "arrivee.badge": "succeeded",
        "arrivee.controle": "succeeded",
    }
    assert actions["arrivee.comptes"]["decisions"][0]["by"] == "policy"
    assert actions["arrivee.groupe_sensible"]["decisions"][0]["by"] == "admin@varga.dev"
    assert actions["arrivee.groupe_sensible"]["decisions"][0]["auth_age_seconds"] is not None
    chronologie = (await client.get(f"/api/v1/work-items/{ticket['work_item']}/timeline")).text
    assert ATTESTATION_BADGE in chronologie, "la preuve de la remise se lit dans le dossier"


async def test_un_depart_a_j0_tout_coupe_le_poste_repris_recu_efface(
    platform: Platform, worker: Any, faux: Faux
) -> None:
    client = platform.client
    await login(client)
    await _organisation(client)
    projet = await _projet_rh(client)
    identifiant = faux.annuaire.ajouter_compte("paul@acme.test")
    faux.annuaire.groupes["devs"] = {identifiant}
    faux.lecteurs.badges["04D5E6F7"] = {"uid": "04D5E6F7", "holder": "paul@acme.test", "active": True}
    faux.parc.postes["PC-0042"] = {"serial": "PC-0042", "upn": "paul@acme.test", "state": "enrolled"}
    debut = await platform.env.get_current_time()
    depart = (debut + timedelta(days=3)).date()
    champs = {"nom": "Paul Durand", "upn": "paul@acme.test", "date_depart": depart.isoformat(),
              "badge_uid": "04D5E6F7", "serial": "PC-0042", "adresse": "8 quai des Brumes, Lyon"}  # fmt: skip
    cree = await client.post(
        f"/api/v1/projects/{projet['id']}/work-items",
        json={"title": "Départ de Paul", "labels": ["depart"], "fields": champs},
    )
    assert cree.status_code == 201 and cree.json()["workflow_name"] == "offboarding", cree.text
    ticket = {"work_item": cree.json()["id"], "key": cree.json()["tracker_key"]}

    async with worker():
        platform.adapters.executor.queue_result(_plan("plan_de_depart"))
        interprete = await _demarrer(platform, projet, ticket)
        await _etat(client, ticket["work_item"], "plan", "le plan de départ est prêt")
        await _demande(client, ticket["work_item"])
        assert (
            await client.post(f"/api/v1/work-items/{ticket['work_item']}/decisions", json={"kind": "approve"})
        ).status_code == 202
        await _attendre(interprete, lambda s: s.get("waiting_until"), "J0 attendu")
        assert faux.annuaire.comptes[identifiant]["accountEnabled"] is True, "rien avant J0"
        await platform.env.sleep(timedelta(days=3, hours=1))
        await _etat(client, ticket["work_item"], "reprise", "les accès sont coupés et la reprise planifiée")
        assert faux.annuaire.comptes[identifiant]["accountEnabled"] is False
        assert faux.annuaire.sessions_revoquees == ["paul@acme.test"]
        assert faux.lecteurs.badges["04D5E6F7"]["active"] is False
        retour = faux.transporteur.envois[f"{ticket['key']}-retour"]
        assert (retour["direction"], retour["address"]) == ("return", champs["adresse"])

        assert (await _demande(client, ticket["work_item"]))["kind"] == "task"
        reception = await client.post(
            f"/api/v1/work-items/{ticket['work_item']}/decisions",
            json={"kind": "complete", "values": {"etat_du_poste": "intact"}, "attested": True},
        )
        assert reception.status_code == 202, reception.text
        resultat = await interprete.result()
    assert resultat["state"] == "clos"
    assert faux.parc.postes["PC-0042"]["state"] == "wiped"
    actions = await _actions(client, projet)
    assert {k: a["status"] for k, a in actions.items()} == {
        "depart.coupure": "succeeded",
        "depart.reprise": "succeeded",
        "depart.effacement": "succeeded",
        "depart.controle": "succeeded",
    }
    chronologie = (await client.get(f"/api/v1/work-items/{ticket['work_item']}/timeline")).text
    assert ATTESTATION_RECEPTION in chronologie
