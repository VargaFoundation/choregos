"""Les agents externes (ADR 0033, S18-06) : un client de la porte MCP rattaché à un agent.

Ses droits sont ceux de l'humain, intersectés avec ce que la version de l'agent nomme ; sa
révocation se relit à chaque appel.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login


async def _agent_externe(client: AsyncClient, outils: list[str] | None, slug: str = "claude-de-lea") -> None:
    spec: dict[str, Any] = {
        "mcp_servers": [{"connector": "choregos", "tools": outils}] if outils is not None else []
    }
    cree = await client.post(
        "/api/v1/orgs/varga/agents",
        json={"slug": slug, "kind": "external", "display_name": "Le Claude Code de Léa", "spec": spec},
    )
    assert cree.status_code == 201, cree.text


async def _jeton(client: AsyncClient, portee: str = "mcp:write") -> tuple[str, str]:
    cree = await client.post("/api/v1/me/tokens", json={"name": "claude-code", "scopes": [portee]})
    assert cree.status_code == 201, cree.text
    return str(cree.json()["id"]), str(cree.json()["token"])


async def _rattacher(client: AsyncClient, jeton_id: str, slug: str = "claude-de-lea") -> Any:
    return await client.post(
        f"/api/v1/orgs/varga/agents/{slug}/credentials", json={"kind": "token", "token_id": jeton_id}
    )


async def _outils(client: AsyncClient, jeton: str) -> Any:
    return await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {jeton}"},
    )


async def test_un_agent_externe_ne_voit_que_ce_que_sa_version_nomme(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _agent_externe(client, ["list_projects", "search_*"])
    jeton_id, jeton = await _jeton(client)
    rattache = await _rattacher(client, jeton_id)
    assert rattache.status_code == 201, rattache.text
    client.cookies.clear()
    noms = {o["name"] for o in (await _outils(client, jeton)).json()["result"]["tools"]}
    assert noms == {"list_projects", "search_work_items"}
    appel = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "create_work_item", "arguments": {}},
        },
        headers={"Authorization": f"Bearer {jeton}"},
    )
    assert appel.json()["error"]["code"] == -32602, "hors de la version de l'agent : comme un outil inconnu"


async def test_un_agent_revoque_recoit_401_a_l_appel_suivant(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _agent_externe(client, None)
    jeton_id, jeton = await _jeton(client)
    await _rattacher(client, jeton_id)
    assert (await _outils(client, jeton)).status_code == 200
    revoque = await client.patch("/api/v1/orgs/varga/agents/claude-de-lea", json={"status": "revoked"})
    assert revoque.status_code == 200
    refuse = await _outils(client, jeton)
    assert refuse.status_code == 401 and "révoqué" in refuse.text


async def test_un_humain_sans_droit_donne_un_agent_sans_droit(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _agent_externe(client, ["list_projects", "create_work_item"])
    membre = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "lecteur@varga.dev", "role": "viewer"}
    )
    assert membre.status_code in {200, 201}
    await login(client, "lecteur@varga.dev")
    jeton_id, jeton = await _jeton(client)
    await login(client, "admin@varga.dev")
    assert (await _rattacher(client, jeton_id)).status_code == 201
    client.cookies.clear()
    noms = {o["name"] for o in (await _outils(client, jeton)).json()["result"]["tools"]}
    assert noms == {"list_projects"}, "la version permet create_work_item, la personne ne le peut pas"


async def test_l_audit_nomme_l_agent(client: AsyncClient, project: dict[str, Any]) -> None:
    await _agent_externe(client, None)
    jeton_id, jeton = await _jeton(client)
    await _rattacher(client, jeton_id)
    appel = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "list_projects"}},
        headers={"Authorization": f"Bearer {jeton}"},
    )
    assert appel.status_code == 200, appel.text
    audit = (await client.get("/api/v1/audit")).json()["items"]
    (ligne,) = [a for a in audit if a["action"] == "mcp.call"]
    assert ligne["payload"]["agent"] == "claude-de-lea"


@pytest.mark.parametrize(
    ("corps", "statut"),
    [
        pytest.param({"kind": "token"}, 422, id="sans-jeton"),
        pytest.param({"kind": "token", "token_id": "inconnu"}, 422, id="jeton-inconnu"),
        pytest.param({"kind": "oauth_client"}, 422, id="sans-client"),
    ],
)
async def test_ce_qui_ne_se_rattache_pas(
    client: AsyncClient, admin: str, corps: dict[str, Any], statut: int
) -> None:
    await _agent_externe(client, None)
    assert (
        await client.post("/api/v1/orgs/varga/agents/claude-de-lea/credentials", json=corps)
    ).status_code == statut


async def test_un_agent_interne_ne_porte_pas_de_client(client: AsyncClient, admin: str) -> None:
    interne = await client.post(
        "/api/v1/orgs/varga/agents", json={"slug": "interne", "display_name": "Interne", "kind": "internal"}
    )
    assert interne.status_code == 201
    jeton_id, _ = await _jeton(client)
    assert (await _rattacher(client, jeton_id, slug="interne")).status_code == 422
    await _agent_externe(client, None)
    assert (await _rattacher(client, jeton_id)).status_code == 201
    assert (await _rattacher(client, jeton_id)).status_code == 409, "un client, un agent"


async def test_un_client_oauth_incarne_son_agent(client: AsyncClient, admin: str) -> None:
    """Le chemin OAuth (S15-08) : l'`azp` du jeton désigne le client rattaché."""
    from choregos_api.db.session import session_scope
    from choregos_api.mcp.appelant import Appelant, incarner_l_agent
    from choregos_api.rbac import Principal

    await _agent_externe(client, ["list_projects"])
    rattache = await client.post(
        "/api/v1/orgs/varga/agents/claude-de-lea/credentials",
        json={"kind": "oauth_client", "client_id": "claude-ai"},
    )
    assert rattache.status_code == 201, rattache.text
    appelant = Appelant(
        principal=Principal(user_id="u", email="x@y"), jeton=None, portees=frozenset({"mcp:read"})
    )
    async with session_scope(orgs="*") as session:
        await incarner_l_agent(session, appelant, client_id="claude-ai")
    assert (appelant.agent, appelant.motifs) == ("claude-de-lea", ("list_projects",))


async def test_un_client_rattache_dit_quand_il_a_appele_et_avec_quoi(client: AsyncClient, admin: str) -> None:
    """La page Agents montre un Claude Code « connecté » (S18-07) : le dernier appel de son jeton à la
    porte, et le client qui l'a fait. Avant tout appel, rien n'est inventé."""
    await _agent_externe(client, None)
    jeton_id, jeton = await _jeton(client)
    assert (await _rattacher(client, jeton_id)).status_code == 201
    (avant,) = (await client.get("/api/v1/orgs/varga/agents/claude-de-lea/credentials")).json()
    assert avant["token_name"] == "claude-code"
    assert avant["last_used_at"] is None and avant["last_client"] is None

    cookies = dict(client.cookies)
    client.cookies.clear()
    appel = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {jeton}", "User-Agent": "claude-code/2.1.0"},
    )
    assert appel.status_code == 200, appel.text
    client.cookies.update(cookies)
    (apres,) = (await client.get("/api/v1/orgs/varga/agents/claude-de-lea/credentials")).json()
    assert apres["last_used_at"] is not None
    assert apres["last_client"] == "claude-code/2.1.0"
