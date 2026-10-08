"""Les faux du scénario RH (S20-04) : ce que la suite de conformité ne dit pas — ce que chacun
REFUSE, nommé, et ce que la mise en scène fait avancer. Idempotence, schémas et secrets :
`tests/conformance/connecteurs/`."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.errors import AdapterError, ConfigurationError
from choregos_adapters.fakes.rh import (
    FakeBadges,
    FakeFournisseur,
    FakeMdm,
    FakeTransporteur,
    serveur_mcp,
)


def test_un_poste_inscrit_pour_un_autre_est_refuse_et_un_poste_efface_se_reinscrit() -> None:
    parc = FakeMdm()
    parc.faire("enroll_device", {"serial": "PC-1", "upn": "lea@acme.test"})
    with pytest.raises(AdapterError, match="inscrit pour un autre utilisateur"):
        parc.faire("enroll_device", {"serial": "PC-1", "upn": "paul@acme.test"})
    with pytest.raises(AdapterError, match="aucun poste PC-2"):
        parc.faire("wipe_device", {"serial": "PC-2"})
    assert parc.faire("wipe_device", {"serial": "PC-1"})["upn"] is None, "effacé, le poste est libéré"
    repris = parc.faire("enroll_device", {"serial": "PC-1", "upn": "paul@acme.test"})
    assert (repris["upn"], repris["state"], repris["enrolled"]) == ("paul@acme.test", "enrolled", True)


def test_une_reference_prise_par_un_autre_envoi_est_refusee_et_le_colis_se_suit() -> None:
    transporteur = FakeTransporteur()
    envoi = transporteur.faire("create_shipment", {"reference": "RH-1", "address": "Lyon", "items": ["PC-1"]})
    assert envoi["state"] == "shipped" and envoi["tracking"].startswith("TRK-")
    with pytest.raises(AdapterError, match="déjà prise"):
        transporteur.faire("create_shipment", {"reference": "RH-1", "address": "Paris"})
    with pytest.raises(AdapterError, match="déjà prise"):
        transporteur.faire("create_return", {"reference": "RH-1", "address": "Lyon"})
    transporteur.livrer("RH-1")
    assert transporteur.faire("shipment_status", {"reference": "RH-1"})["state"] == "delivered"
    retour = transporteur.faire("create_return", {"reference": "RH-1-retour", "address": "Lyon"})
    assert retour["tracking"].startswith("RET-") and retour["state"] == "collection_booked"


def test_un_badge_actif_pour_un_autre_est_refuse_et_un_badge_inconnu_ne_se_coupe_pas() -> None:
    badges = FakeBadges()
    badges.faire("activate_badge", {"uid": "04A1", "holder": "lea@acme.test"})
    with pytest.raises(AdapterError, match="actif pour un autre porteur"):
        badges.faire("activate_badge", {"uid": "04A1", "holder": "paul@acme.test"})
    # Un UID mal saisi au départ : le refus dit que le vrai badge ouvre encore.
    with pytest.raises(AdapterError, match="aucun badge 04B2"):
        badges.faire("deactivate_badge", {"uid": "04B2"})
    assert badges.faire("deactivate_badge", {"uid": "04A1"})["deactivated"] is True
    assert badges.faire("badge_status", {"uid": "04A1"})["active"] is False


def test_un_argument_inconnu_ou_une_operation_inconnue_sont_refuses_nommes() -> None:
    with pytest.raises(AdapterError, match="enroll_device"):
        FakeMdm().faire("enroll_device", {"serial": "PC-1", "upn": "x", "couleur": "bleu"})
    with pytest.raises(AdapterError, match="pas d'opération format_disk"):
        FakeMdm().faire("format_disk", {})


async def test_l_agent_du_fournisseur_commande_suit_et_refuse_en_mcp() -> None:
    from choregos_adapters.mcp import ClientMcp

    fournisseur = FakeFournisseur()
    client = ClientMcp(
        "https://fournisseur.test/mcp",
        token="cle",
        transport=serveur_mcp(fournisseur, jeton="cle").transport(),
    )
    outils = {o.name: o for o in await client.list_tools()}
    assert {n: o.read_only for n, o in outils.items()} == {"commander_poste": False, "suivi_commande": True}
    commande = await client.call_tool("commander_poste", {"reference": "RH-1", "modele": "p14", "upn": "lea"})
    serie = commande["structuredContent"]["serial"]
    assert serie.startswith("PC-") and commande["structuredContent"]["state"] == "confirmed"
    fournisseur.expedier("RH-1")
    suivi: dict[str, Any] = (await client.call_tool("suivi_commande", {"reference": "RH-1"}))[
        "structuredContent"
    ]
    assert (suivi["state"], suivi["serial"], suivi["tracking"]) == ("shipped", serie, f"COLIS-{serie[3:]}")
    # Un refus du fournisseur est un résultat d'outil en erreur, pas une erreur du protocole.
    refus = await client.call_tool("commander_poste", {"reference": "RH-1", "modele": "p16", "upn": "lea"})
    assert refus["isError"] is True and "pour un autre poste" in refus["content"][0]["text"]
    assert len(fournisseur.commandes) == 1


def test_les_familles_sont_des_types_de_connecteurs_et_demo_est_refuse_en_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_adapters import build, connector_types

    demos = {
        k: {o.name: o.default_policy for o in s.operations} for k, t, s in connector_types() if t == "demo"
    }
    # Le `cd` de démonstration (S21-24) n'expose rien : c'est le train qui l'appelle, jamais un agent.
    assert demos.pop("cd") == {}
    familles = demos
    assert familles == {
        "mdm": {"enroll_device": "approval", "wipe_device": "approval", "device_status": "allowed"},
        "shipping": {
            "create_shipment": "approval",
            "create_return": "approval",
            "shipment_status": "allowed",
        },
        "access_control": {
            "activate_badge": "approval",
            "deactivate_badge": "approval",
            "badge_status": "allowed",
        },
    }
    monkeypatch.setenv("CHOREGOS_FAKES", "0")
    monkeypatch.setenv("CHOREGOS_ENV", "prod")
    with pytest.raises(ConfigurationError, match="`mdm: demo` refusé en prod"):
        build("mdm", "demo", {})
    monkeypatch.setenv("CHOREGOS_ENV", "dev")
    assert type(build("mdm", "demo", {}).faux).__name__ == "FakeMdm"
