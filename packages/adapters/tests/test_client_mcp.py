"""Le client MCP de la plateforme (ADR 0034, S19-03) contre un faux serveur scriptable."""

from __future__ import annotations

import base64

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
    with pytest.raises(ErreurMcp, match="refused by the server"):
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


async def test_basic_sends_the_email_and_token_pair_and_never_bearer() -> None:
    """Atlassian's Rovo MCP Server refuses Bearer for an API token (S39-08a)."""
    serveur = FakeMcpServer(outils=OUTILS[:1])
    client = ClientMcp(
        "https://mcp.atlassian.com/v1/mcp",
        token="api-token",
        auth_scheme="basic",
        username="bot@example.com",
        transport=serveur.transport(),
    )
    await client.list_tools()
    expected = "Basic " + base64.b64encode(b"bot@example.com:api-token").decode()
    assert serveur.recues
    for methode, entetes, _ in serveur.recues:
        assert entetes["authorization"] == expected, methode
        assert "bearer" not in entetes["authorization"].lower()


async def test_the_default_scheme_is_still_bearer() -> None:
    serveur = FakeMcpServer(outils=OUTILS[:1])
    await ClientMcp("https://f.test/mcp", token="t", transport=serveur.transport()).list_tools()
    assert all(entetes["authorization"] == "Bearer t" for _, entetes, _ in serveur.recues)


def test_basic_without_a_username_or_an_unknown_scheme_is_refused() -> None:
    with pytest.raises(ValueError, match="username"):
        ClientMcp("https://f.test/mcp", token="t", auth_scheme="basic")
    with pytest.raises(ValueError, match="scheme"):
        ClientMcp("https://f.test/mcp", token="t", auth_scheme="digest")


def test_the_connector_type_builds_a_basic_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_adapters import build

    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    client = build(
        "mcp",
        "mcp",
        {
            "url": "https://mcp.atlassian.com/v1/mcp",
            "token": "t",
            "auth_scheme": "basic",
            "username": "a@b.c",
        },
    )
    assert client._headers()["Authorization"].startswith("Basic ")


async def test_a_custom_auth_header_carries_the_raw_key_and_no_authorization() -> None:
    """`auth_header: X-API-Key` (the catalogue's `header:<Name>` entries) sends the key as it is (S39-10c)."""
    serveur = FakeMcpServer(outils=OUTILS[:1])
    client = ClientMcp(
        "https://f.test/mcp", token="the-key", auth_header="X-API-Key", transport=serveur.transport()
    )
    await client.list_tools()
    assert serveur.recues
    for methode, entetes, _ in serveur.recues:
        assert entetes["x-api-key"] == "the-key", methode
        assert "authorization" not in entetes, methode


async def test_the_default_header_is_still_a_bearer() -> None:
    serveur = FakeMcpServer(outils=OUTILS[:1])
    await ClientMcp("https://f.test/mcp", token="t", transport=serveur.transport()).list_tools()
    assert all(entetes["authorization"] == "Bearer t" for _, entetes, _ in serveur.recues)


async def test_le_user_agent_de_la_plateforme_part_avec_chaque_requete() -> None:
    """ADR 0070: a server's operator sees who calls, and how to reach them."""
    serveur = FakeMcpServer(outils=OUTILS[:1])
    agent = "Choregos/1.2 (+https://ops.example.com)"
    client = ClientMcp("https://fournisseur.test/mcp", transport=serveur.transport(), user_agent=agent)
    await client.call_tool("lire_commande", {})
    assert serveur.recues
    for methode, entetes, _ in serveur.recues:
        assert entetes["user-agent"] == agent, methode


async def test_sans_user_agent_le_client_garde_celui_de_httpx() -> None:
    serveur = FakeMcpServer(outils=OUTILS[:1])
    await ClientMcp("https://fournisseur.test/mcp", transport=serveur.transport()).call_tool(
        "lire_commande", {}
    )
    assert all("choregos" not in e["user-agent"].lower() for _, e, _ in serveur.recues)
