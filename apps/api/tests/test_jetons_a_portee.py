"""Les jetons d'API ont une portée, et un jeton MCP ne sert qu'à la porte MCP (ADR 0030).

Un jeton glissé dans la configuration d'un client MCP (Claude Code, Cursor, VS Code…) y vit en
clair. Volé là, il ne doit ouvrir que ce pour quoi il a été frappé : ni l'API REST, ni l'émission
d'un autre jeton, ni une décision. Et lié à un projet, il ne voit que lui.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .conftest import login


async def _frapper(client: AsyncClient, **corps: Any) -> Any:
    reponse = await client.post("/api/v1/me/tokens", json={"name": "test", **corps})
    return reponse


async def test_par_defaut_un_jeton_ouvre_l_api_rest(client: AsyncClient, admin: str) -> None:
    """Les jetons d'avant les portées, et ceux de la CLI, gardent tout : `*`."""
    cree = (await _frapper(client)).json()
    assert cree["scopes"] == ["*"]
    client.cookies.clear()
    reponse = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {cree['token']}"})
    assert reponse.status_code == 200, reponse.text


async def test_un_jeton_mcp_est_refuse_par_l_api_rest(client: AsyncClient, admin: str) -> None:
    cree = (await _frapper(client, scopes=["mcp:read"])).json()
    assert cree["scopes"] == ["mcp:read"]
    client.cookies.clear()
    entete = {"Authorization": f"Bearer {cree['token']}"}
    for methode, chemin, corps in (
        ("GET", "/api/v1/me", None),
        ("GET", "/api/v1/orgs/varga/projects", None),
        ("POST", "/api/v1/me/tokens", {"name": "un autre"}),
    ):
        reponse = await client.request(methode, chemin, headers=entete, json=corps)
        assert reponse.status_code == 403, f"{methode} {chemin} : {reponse.status_code} {reponse.text}"
        assert "porte MCP" in reponse.json()["detail"]


async def test_toute_l_api_ne_se_combine_pas(client: AsyncClient, admin: str) -> None:
    reponse = await _frapper(client, scopes=["*", "mcp:read"])
    assert reponse.status_code == 422, reponse.text


async def test_un_projet_ne_borne_qu_un_jeton_mcp(client: AsyncClient, project: dict[str, Any]) -> None:
    reponse = await _frapper(client, scopes=["*"], project="varga:billing-api")
    assert reponse.status_code == 422, reponse.text


async def test_un_jeton_mcp_lie_a_un_projet(client: AsyncClient, project: dict[str, Any]) -> None:
    cree = await _frapper(client, scopes=["mcp:write"], project="varga:billing-api")
    assert cree.status_code == 201, cree.text
    assert cree.json()["project"] == "varga:billing-api"
    listes = (await client.get("/api/v1/me/tokens")).json()
    assert [(t["scopes"], t["project"]) for t in listes] == [(["mcp:write"], "varga:billing-api")]


async def test_un_projet_inconnu_ou_illisible_ne_se_lie_pas(client: AsyncClient, project: dict[str, Any]) -> None:
    reponse = await _frapper(client, scopes=["mcp:read"], project="varga:nexiste-pas")
    assert reponse.status_code == 404, reponse.text


async def test_le_dernier_client_est_note(client: AsyncClient, admin: str) -> None:
    cree = (await _frapper(client)).json()
    jeton = cree["token"]
    client.cookies.clear()
    await client.get("/api/v1/me", headers={"Authorization": f"Bearer {jeton}", "User-Agent": "claude-code/2.1"})
    # Relu par une session : relire avec le jeton noterait le client de la relecture.
    await login(client, admin)
    listes = (await client.get("/api/v1/me/tokens")).json()
    assert listes[0]["last_client"] == "claude-code/2.1"
    assert listes[0]["last_used_at"] is not None
