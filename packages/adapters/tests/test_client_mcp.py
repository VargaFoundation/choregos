"""Le client MCP de la plateforme (ADR 0034, S19-03) contre un faux serveur scriptable."""

from __future__ import annotations

import pytest
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_adapters.mcp import ClientMcp, ErreurMcp, empreinte_du_schema

OUTILS = [
    {
        "name": "lire_commande",
        "description": "lit",
        "inputSchema": {"type": "object"},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "commander_poste",
        "description": "commande un PC",
        "inputSchema": {"type": "object", "properties": {"modele": {"type": "string"}}},
    },
    {"name": "suivi_commande", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": True}},
    {"name": "annuler_commande", "inputSchema": {"type": "object"}},
    {"name": "retour_materiel", "inputSchema": {"type": "object"}, "annotations": {"readOnlyHint": False}},
]


@pytest.mark.parametrize("sse", [False, True], ids=["json", "sse"])
async def test_tools_list_suit_chaque_page_en_json_comme_en_sse(sse: bool) -> None:
    serveur = FakeMcpServer(outils=OUTILS, par_page=2, sse=sse)
    client = ClientMcp("https://fournisseur.test/mcp", transport=serveur.transport())
    outils = await client.list_tools()
    assert [o.name for o in outils] == [o["name"] for o in OUTILS], "les cinq, sur trois pages"
    assert {o.name for o in outils if o.read_only} == {"lire_commande", "suivi_commande"}
    assert client.serveur["serverInfo"]["name"] == "fake-mcp"
    methodes = [m for m, _, _ in serveur.recues]
    assert methodes[:2] == ["initialize", "notifications/initialized"]
    assert methodes.count("tools/list") == 3


async def test_la_session_et_le_jeton_du_connecteur_partent_avec_chaque_requete() -> None:
    serveur = FakeMcpServer(outils=OUTILS[:1], jeton="cle-du-connecteur")
    client = ClientMcp(
        "https://fournisseur.test/mcp", token="cle-du-connecteur", transport=serveur.transport()
    )
    resultat = await client.call_tool("lire_commande", {"numero": "42"})
    assert resultat["content"][0]["text"] == "lire_commande fait"
    assert serveur.appels == [("lire_commande", {"numero": "42"})]
    for methode, entetes, _ in serveur.recues:
        assert entetes["authorization"] == "Bearer cle-du-connecteur", methode
        if methode != "initialize":
            assert entetes["mcp-session-id"] == "session-1", methode
        assert "cookie" not in entetes


async def test_un_refus_ou_une_erreur_du_serveur_est_une_erreur_nommee() -> None:
    serveur = FakeMcpServer(outils=OUTILS[:1], jeton="attendu")
    with pytest.raises(ErreurMcp, match="refusé"):
        await ClientMcp("https://f.test/mcp", token="autre", transport=serveur.transport()).list_tools()
    serveur = FakeMcpServer(outils=OUTILS[:1])
    with pytest.raises(ErreurMcp, match="outil inconnu"):
        await ClientMcp("https://f.test/mcp", transport=serveur.transport()).call_tool("absent", {})


def test_l_empreinte_d_un_schema_change_avec_le_moindre_ecart() -> None:
    a = {"type": "object", "properties": {"modele": {"type": "string"}}}
    b = {"properties": {"modele": {"type": "string"}}, "type": "object"}
    assert empreinte_du_schema(a) == empreinte_du_schema(b), "l'ordre des clés ne compte pas"
    elargi = {"type": "object", "properties": {"modele": {"type": "string"}, "quantite": {"type": "integer"}}}
    assert empreinte_du_schema(a) != empreinte_du_schema(elargi)
