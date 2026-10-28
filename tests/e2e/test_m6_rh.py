"""M6 — the HR scenario end to end (S20-07), on fakes, in accelerated time.

Joiner: an HR person's Claude files the request through the MCP door; the registry agent prepares
the access plan; an HR person validates it; at D-10 the account and its group are born in the
directory — the organisation's policy has decided; the sensitive group waits for a re-authenticated
person; at D-7 the laptop is ordered from the supplier's agent (MCP, under approval), enrolled in
the device fleet under the serial number the supplier assigned, and shipped; reception hands over
the badge — a task, attested — and the platform activates it; at D+1 it checks that everything holds.

Leaver: at D0 the account, its sessions and the badge are cut in one gesture; the laptop is taken
back, received — a task — and wiped; the platform checks.

Each building block is proven on its fake: removing one turns the scenario red.
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
    """The systems the HR scenario touches, each with the key it expects."""

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
        monkeypatch.setattr(choregos_adapters, "FAKE_MCP", self.serveur)
        for famille, faux in (
            ("mdm", self.parc),
            ("shipping", self.transporteur),
            ("access_control", self.lecteurs),
        ):
            monkeypatch.setitem(choregos_adapters.FAUX_METIER, famille, faux)
        monkeypatch.setenv("CONNECTOR_E2E_CLE_ANNUAIRE", CLE_ANNUAIRE)
        monkeypatch.setenv("CONNECTOR_E2E_CLE_PARC", CLE_PARC)
        monkeypatch.setenv("CONNECTOR_E2E_CLE_FOURNISSEUR", CLE_FOURNISSEUR)


@pytest.fixture
def faux(monkeypatch: pytest.MonkeyPatch) -> Faux:
    return Faux(monkeypatch)


async def _organisation(client: AsyncClient) -> None:
    """The administrator's gesture: the five connectors the template announces, their keys by
    reference, and what the organisation allows without a human decision."""
    for corps in (
        {"name": "directory", "kind": "identity", "type": "entra",
         "config": {"tenant_id": "acme", "client_id": "choregos"},
         "secret_refs": {"client_secret": "env:CONNECTOR_E2E_CLE_ANNUAIRE"}},
        {"name": "devices", "kind": "mdm", "type": "demo",
         "secret_refs": {"api_key": "env:CONNECTOR_E2E_CLE_PARC"}},
        {"name": "carrier", "kind": "shipping", "type": "demo",
         "secret_refs": {"api_key": "env:CONNECTOR_E2E_CLE_PARC"}},
        {"name": "badge-readers", "kind": "access_control", "type": "demo",
         "secret_refs": {"api_key": "env:CONNECTOR_E2E_CLE_PARC"}},
        {"name": "supplier", "kind": "mcp", "type": "mcp", "config": {"url": "https://supplier.test/mcp"},
         "secret_refs": {"token": "env:CONNECTOR_E2E_CLE_FOURNISSEUR"}},
    ):  # fmt: skip
        cree = await client.post(f"{ORG}/connectors", json=corps)
        assert cree.status_code == 201, cree.text
    # The supplier's tools are DISCOVERED, and are born closed.
    decouverte = await client.post(f"{ORG}/connectors/supplier/discover")
    assert decouverte.status_code == 200, decouverte.text
    operations = {
        o["name"]: o["policy"] for o in (await client.get(f"{ORG}/connectors/supplier")).json()["operations"]
    }
    assert operations == {"order_laptop": "forbidden", "order_status": "forbidden"}, (
        "a discovered tool is born closed"
    )
    permises = {
        "directory": ["create_user", "add_to_group", "remove_from_group", "disable_user", "revoke_sessions"],
        "devices": ["enroll_device", "wipe_device"],
        "carrier": ["create_shipment", "create_return"],
        "badge-readers": ["activate_badge", "deactivate_badge"],
    }
    for connecteur, noms in permises.items():
        for nom in noms:
            ouverte = await client.patch(
                f"{ORG}/connectors/{connecteur}/operations/{nom}", json={"policy": "allowed"}
            )
            assert ouverte.status_code == 200, ouverte.text
    # A purchase is decided: the laptop order stays under approval.
    sous_validation = await client.patch(
        f"{ORG}/connectors/supplier/operations/order_laptop", json={"policy": "approval"}
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
    """The request, filed by an HR person's Claude: an `mcp:write` token, the project's door."""
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
                             "arguments": {"title": f"Arrival of {champs['full_name']}",
                                           "workflow": "onboarding", "fields": champs}}},
        )  # fmt: skip
    finally:
        await claude.aclose()
    assert appel.status_code == 200, appel.text
    resultat = appel.json()["result"]
    assert resultat["isError"] is False, resultat
    return dict(resultat["structuredContent"])


async def _demarrer(platform: Platform, projet: dict[str, Any], ticket: dict[str, Any]) -> Any:
    """The scenario starts the interpreter on the workers' queue, under the API's identifier."""
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
    raise AssertionError(f"{quoi}: the ticket is in `{lu['state']}`, not in `{attendu}`")


async def _demande(client: AsyncClient, ticket_id: str) -> dict[str, Any]:
    """The human request the interpreter has just opened on the ticket."""
    for _ in range(400):
        lu = (await client.get(f"/api/v1/work-items/{ticket_id}")).json()
        if lu.get("pending_request"):
            return dict(lu["pending_request"])
        await asyncio.sleep(0.05)
    raise AssertionError("no human request open")


async def _attendre(handle: Any, predicat: Any, quoi: str) -> dict[str, Any]:
    for _ in range(400):
        statut = await handle.query("status")
        if predicat(statut):
            return dict(statut)
        await asyncio.sleep(0.05)
    raise AssertionError(f"{quoi}: never true ({await handle.query('status')})")


async def _approuver_l_action(client: AsyncClient, projet: dict[str, Any], genre: str) -> dict[str, Any]:
    """The pending action of this kind, approved by a re-authenticated person."""
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
        raise AssertionError(f"no `{genre}` action pending")
    decidee = await client.post(
        f"/api/v1/projects/{projet['id']}/actions/{trouvees[0]['id']}/decision", json={"decision": "approve"}
    )
    assert decidee.status_code == 200 and decidee.json()["status"] == "approved", decidee.text
    return dict(decidee.json())


async def _actions(client: AsyncClient, projet: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {a["kind"]: a for a in (await client.get(f"/api/v1/projects/{projet['id']}/actions")).json()}


def _plan(nom: str) -> StageResult:
    return StageResult(
        status=StageStatus.DONE, summary="plan ready", outputs={nom: f"## {nom}\n- nothing unusual"}
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
        "full_name": "Léa Martin",
        "upn": "lea@acme.test",
        "start_date": arrivee.isoformat(),
        "job": "Developer",
        "group": "devs",
        "sensitive_group": "prod-read",
        "address": "12 Peace Street, Paris",
    }
    ticket = await _par_claude(platform, client, champs)

    async with worker():
        platform.adapters.executor.queue_result(_plan("access_plan"))
        interprete = await _demarrer(platform, projet, ticket)
        await _etat(client, ticket["work_item"], "planned", "the registry agent prepared the plan")
        await _demande(client, ticket["work_item"])
        assert (
            await client.post(f"/api/v1/work-items/{ticket['work_item']}/decisions", json={"kind": "approve"})
        ).status_code == 202

        # D-10: the policy allowed the account and its group — the action is born approved and plays.
        statut = await _attendre(interprete, lambda s: s.get("waiting_until"), "D-10 awaited")
        assert statut["waiting_until"].startswith((arrivee - timedelta(days=10)).isoformat())
        assert faux.annuaire.comptes == {}, "nothing before D-10"
        await platform.env.sleep(timedelta(days=5, hours=1))
        await _etat(client, ticket["work_item"], "accounts_created", "the account is created at D-10")
        (compte,) = faux.annuaire.comptes.values()
        assert (
            compte["userPrincipalName"] == "lea@acme.test"
            and compte["id"] in faux.annuaire.membres_de_l_unite
        )
        assert compte["id"] in faux.annuaire.groupes["devs"]

        # The sensitive group: a re-authenticated person.
        await _approuver_l_action(client, projet, "onboarding.sensitive_group")
        await _etat(client, ticket["work_item"], "sensitive_granted", "the sensitive group is decided")
        assert compte["id"] in faux.annuaire.groupes["prod-read"]

        # D-7: the supplier order is decided; the fleet and the shipment follow its serial number.
        await _attendre(interprete, lambda s: s.get("waiting_until"), "D-7 awaited")
        assert faux.fournisseur.commandes == {}
        await platform.env.sleep(timedelta(days=3))
        await _approuver_l_action(client, projet, "onboarding.laptop")
        await _etat(client, ticket["work_item"], "laptop_shipped", "the laptop is ordered, enrolled, shipped")
        (commande,) = faux.fournisseur.commandes.values()
        serie = commande["serial"]
        assert faux.parc.postes[serie] == {"serial": serie, "upn": "lea@acme.test", "state": "enrolled"}
        (envoi,) = faux.transporteur.envois.values()
        assert (envoi["items"], envoi["address"]) == ([serie], champs["address"])
        assert {e["authorization"] for _, e, _ in faux.serveur.recues} == {f"Bearer {CLE_FOURNISSEUR}"}

        # The badge: a task, its proof; then the platform activates it.
        demande = await _demande(client, ticket["work_item"])
        assert (demande["kind"], demande["payload"]["attest"]) == ("task", ATTESTATION_BADGE)
        tache = await client.post(
            f"/api/v1/work-items/{ticket['work_item']}/decisions",
            json={"kind": "complete", "values": {"badge_uid": "04A1B2C3"}, "attested": True},
        )
        assert tache.status_code == 202, tache.text
        await _etat(client, ticket["work_item"], "active", "the badge is active")
        assert faux.lecteurs.badges["04A1B2C3"] == {
            "uid": "04A1B2C3",
            "holder": "lea@acme.test",
            "active": True,
        }

        # D+1: the platform re-reads and checks.
        await platform.env.sleep(timedelta(days=12))
        resultat = await interprete.result()
    assert resultat["state"] == "ready"

    actions = await _actions(client, projet)
    assert {k: a["status"] for k, a in actions.items()} == {
        "onboarding.accounts": "succeeded",
        "onboarding.sensitive_group": "succeeded",
        "onboarding.laptop": "succeeded",
        "onboarding.badge": "succeeded",
        "onboarding.check": "succeeded",
    }
    assert actions["onboarding.accounts"]["decisions"][0]["by"] == "policy"
    assert actions["onboarding.sensitive_group"]["decisions"][0]["by"] == "admin@varga.dev"
    assert actions["onboarding.sensitive_group"]["decisions"][0]["auth_age_seconds"] is not None
    chronologie = (await client.get(f"/api/v1/work-items/{ticket['work_item']}/timeline")).text
    assert ATTESTATION_BADGE in chronologie, "the proof of the handover can be read in the case file"


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
    champs = {"full_name": "Paul Durand", "upn": "paul@acme.test", "last_day": depart.isoformat(),
              "badge_uid": "04D5E6F7", "serial": "PC-0042", "address": "8 Fog Quay, Lyon"}  # fmt: skip
    cree = await client.post(
        f"/api/v1/projects/{projet['id']}/work-items",
        json={"title": "Departure of Paul", "labels": ["leaver"], "fields": champs},
    )
    assert cree.status_code == 201 and cree.json()["workflow_name"] == "offboarding", cree.text
    ticket = {"work_item": cree.json()["id"], "key": cree.json()["tracker_key"]}

    async with worker():
        platform.adapters.executor.queue_result(_plan("leaver_plan"))
        interprete = await _demarrer(platform, projet, ticket)
        await _etat(client, ticket["work_item"], "planned", "the leaver plan is ready")
        await _demande(client, ticket["work_item"])
        assert (
            await client.post(f"/api/v1/work-items/{ticket['work_item']}/decisions", json={"kind": "approve"})
        ).status_code == 202
        await _attendre(interprete, lambda s: s.get("waiting_until"), "D0 awaited")
        assert faux.annuaire.comptes[identifiant]["accountEnabled"] is True, "nothing before D0"
        await platform.env.sleep(timedelta(days=3, hours=1))
        await _etat(
            client, ticket["work_item"], "pickup_scheduled", "the access is cut and the pickup scheduled"
        )
        assert faux.annuaire.comptes[identifiant]["accountEnabled"] is False
        assert faux.annuaire.sessions_revoquees == ["paul@acme.test"]
        assert faux.lecteurs.badges["04D5E6F7"]["active"] is False
        retour = faux.transporteur.envois[f"{ticket['key']}-return"]
        assert (retour["direction"], retour["address"]) == ("return", champs["address"])

        assert (await _demande(client, ticket["work_item"]))["kind"] == "task"
        reception = await client.post(
            f"/api/v1/work-items/{ticket['work_item']}/decisions",
            json={"kind": "complete", "values": {"laptop_condition": "intact"}, "attested": True},
        )
        assert reception.status_code == 202, reception.text
        resultat = await interprete.result()
    assert resultat["state"] == "closed"
    assert faux.parc.postes["PC-0042"]["state"] == "wiped"
    actions = await _actions(client, projet)
    assert {k: a["status"] for k, a in actions.items()} == {
        "offboarding.cut_access": "succeeded",
        "offboarding.pickup": "succeeded",
        "offboarding.wipe": "succeeded",
        "offboarding.check": "succeeded",
    }
    chronologie = (await client.get(f"/api/v1/work-items/{ticket['work_item']}/timeline")).text
    assert ATTESTATION_RECEPTION in chronologie
