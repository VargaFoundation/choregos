"""La porte MCP des clients externes (ADR 0030) : transport, jetons, outils, garde-fous.

Le client qu'on simule est celui d'une personne — Claude Code, Cursor, VS Code — configuré avec un
jeton à portée MCP. Trois propriétés tiennent la porte, et chacune a ses tests : elle refuse en HTTP
ce qui n'est pas un échange MCP acceptable ; elle n'annonce que ce que la personne a le droit de
faire ; et aucun de ses outils ne décide.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from httpx import AsyncClient, Response

from .conftest import login

ADMIN = "admin@varga.dev"


async def _jeton(client: AsyncClient, *portees: str, projet: str | None = None) -> str:
    corps: dict[str, Any] = {"name": "claude-code", "scopes": list(portees)}
    if projet:
        corps["project"] = projet
    cree = await client.post("/api/v1/me/tokens", json=corps)
    assert cree.status_code == 201, cree.text
    return str(cree.json()["token"])


async def _rpc(
    client: AsyncClient,
    jeton: str,
    methode: str,
    params: dict[str, Any] | None = None,
    *,
    chemin: str = "/mcp",
    entetes: dict[str, str] | None = None,
) -> Response:
    message: dict[str, Any] = {"jsonrpc": "2.0", "id": 1, "method": methode}
    if params is not None:
        message["params"] = params
    return await client.post(
        chemin, json=message, headers={"Authorization": f"Bearer {jeton}", **(entetes or {})}
    )


async def _outil(client: AsyncClient, jeton: str, nom: str, chemin: str = "/mcp", **arguments: Any) -> Any:
    reponse = await _rpc(client, jeton, "tools/call", {"name": nom, "arguments": arguments}, chemin=chemin)
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


async def _tracker_interne(client: AsyncClient, project: dict[str, Any]) -> None:
    reponse = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert reponse.status_code == 200, reponse.text


# ───────────────────────────── transport ─────────────────────────────


async def test_sans_flux_ni_session(client: AsyncClient) -> None:
    for methode in ("GET", "DELETE"):
        reponse = await client.request(methode, "/mcp")
        assert reponse.status_code == 405, reponse.text
        assert reponse.headers["allow"] == "POST"


async def test_sans_jeton_un_401_qui_dit_comment_s_authentifier(client: AsyncClient) -> None:
    reponse = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert reponse.status_code == 401
    assert reponse.headers["www-authenticate"].startswith("Bearer")


async def test_un_jeton_de_toute_l_api_est_refuse(client: AsyncClient, admin: str) -> None:
    """Un jeton `*` ouvre l'API REST : on n'en veut pas en clair dans la configuration d'un client."""
    jeton = await _jeton(client, "*")
    client.cookies.clear()
    reponse = await _rpc(client, jeton, "ping")
    assert reponse.status_code == 403, reponse.text
    assert 'error="insufficient_scope"' in reponse.headers["www-authenticate"]


async def test_une_origine_etrangere_est_refusee(client: AsyncClient, admin: str) -> None:
    """Une page web ouverte dans le navigateur ne parle pas à la place de la personne."""
    jeton = await _jeton(client, "mcp:read")
    client.cookies.clear()
    reponse = await _rpc(client, jeton, "ping", entetes={"Origin": "https://ailleurs.example"})
    assert reponse.status_code == 403, reponse.text


async def test_un_message_trop_grand_est_refuse(client: AsyncClient, admin: str) -> None:
    jeton = await _jeton(client, "mcp:read")
    client.cookies.clear()
    reponse = await client.post(
        "/mcp",
        content=b"x" * (1024 * 1024 + 1),
        headers={"Authorization": f"Bearer {jeton}", "Content-Type": "application/json"},
    )
    assert reponse.status_code == 413


async def test_initialize_negocie_la_version_et_pose_la_regle(client: AsyncClient, admin: str) -> None:
    jeton = await _jeton(client, "mcp:read")
    client.cookies.clear()
    connue = (await _rpc(client, jeton, "initialize", {"protocolVersion": "2025-06-18"})).json()["result"]
    assert connue["protocolVersion"] == "2025-06-18"
    assert connue["serverInfo"]["name"] == "choregos"
    assert "decision_url" in connue["instructions"], "la règle « jamais de décision ici » est dite au modèle"
    inconnue = (await _rpc(client, jeton, "initialize", {"protocolVersion": "1999-01-01"})).json()["result"]
    assert inconnue["protocolVersion"] == "2025-11-25"


async def test_une_notification_est_acceptee_sans_corps(client: AsyncClient, admin: str) -> None:
    jeton = await _jeton(client, "mcp:read")
    client.cookies.clear()
    reponse = await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={"Authorization": f"Bearer {jeton}"},
    )
    assert reponse.status_code == 202
    assert reponse.content == b""


async def test_ce_qui_n_est_pas_une_requete_acceptable(client: AsyncClient, admin: str) -> None:
    jeton = await _jeton(client, "mcp:read")
    client.cookies.clear()
    entete = {"Authorization": f"Bearer {jeton}"}
    lot = await client.post("/mcp", json=[{"jsonrpc": "2.0", "id": 1, "method": "ping"}], headers=entete)
    assert lot.status_code == 400 and lot.json()["error"]["code"] == -32600
    version = await _rpc(client, jeton, "ping", entetes={"MCP-Protocol-Version": "1999-01-01"})
    assert version.status_code == 400
    inconnue = await _rpc(client, jeton, "resources/list")
    assert inconnue.json()["error"]["code"] == -32601


# ───────────────────────────── ce qui s'annonce ─────────────────────────────


async def test_un_jeton_de_lecture_ne_voit_aucun_outil_d_ecriture(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    jeton = await _jeton(client, "mcp:read")
    client.cookies.clear()
    noms = {o["name"] for o in (await _rpc(client, jeton, "tools/list")).json()["result"]["tools"]}
    assert "create_work_item" not in noms
    assert {"list_projects", "get_work_item", "list_pending_decisions"} <= noms
    appel = await _rpc(client, jeton, "tools/call", {"name": "create_work_item", "arguments": {"title": "x"}})
    assert appel.json()["error"]["code"] == -32602, "un outil hors des droits répond comme un outil inconnu"


async def test_aucun_outil_ne_decide(client: AsyncClient, project: dict[str, Any]) -> None:
    jeton = await _jeton(client, "mcp:write")
    client.cookies.clear()
    outils = (await _rpc(client, jeton, "tools/list")).json()["result"]["tools"]
    assert "create_work_item" in {o["name"] for o in outils}
    decideurs = [o["name"] for o in outils if re.search(r"decid|approv|reject|answer", o["name"])]
    assert not decideurs, f"la porte ne décide jamais : {decideurs}"
    lecture = {o["name"]: o["annotations"]["readOnlyHint"] for o in outils}
    assert lecture["create_work_item"] is False and lecture["get_work_item"] is True


async def test_la_porte_d_un_projet_ne_demande_pas_le_projet(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    jeton = await _jeton(client, "mcp:write")
    client.cookies.clear()
    outils = (await _rpc(client, jeton, "tools/list", chemin="/mcp/projects/varga:billing-api")).json()[
        "result"
    ]["tools"]
    creer = next(o for o in outils if o["name"] == "create_work_item")
    assert "project" not in creer["inputSchema"]["properties"]


# ───────────────────────────── les outils ─────────────────────────────


async def test_list_projects_rend_les_liens_de_la_console(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    jeton = await _jeton(client, "mcp:read")
    client.cookies.clear()
    resultat = (await _outil(client, jeton, "list_projects"))["result"]
    assert resultat["isError"] is False
    projets = resultat["structuredContent"]["projects"]
    assert [p["project"] for p in projets] == ["varga:billing-api"]
    assert projets[0]["console_url"].endswith("/p/billing-api")


async def test_create_work_item_ouvre_un_ticket_au_nom_de_la_personne(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _tracker_interne(client, project)
    jeton = await _jeton(client, "mcp:write")
    client.cookies.clear()
    resultat = (
        await _outil(client, jeton, "create_work_item", project="varga:billing-api", title="Depuis Claude")
    )["result"]
    assert resultat["isError"] is False, resultat
    cle = resultat["structuredContent"]["key"]
    assert resultat["structuredContent"]["console_url"].endswith(
        f"/items/{resultat['structuredContent']['work_item']}"
    )

    await login(client, ADMIN)
    tickets = (await client.get(f"/api/v1/projects/{project['id']}/work-items")).json()["items"]
    assert [t["tracker_key"] for t in tickets] == [cle]
    audit = (await client.get("/api/v1/audit")).json()["items"]
    actions = {(a["action"], a.get("target_id")) for a in audit}
    assert ("mcp.call", "create_work_item") in actions
    assert ("workitem.create", resultat["structuredContent"]["work_item"]) in actions


async def test_un_tracker_externe_est_un_refus_lisible(client: AsyncClient, project: dict[str, Any]) -> None:
    reponse = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker",
        json={"type": "github-issues", "config": {"repo": "varga/billing-api"}},
    )
    assert reponse.status_code == 200, reponse.text
    jeton = await _jeton(client, "mcp:write")
    client.cookies.clear()
    resultat = (await _outil(client, jeton, "create_work_item", project="varga:billing-api", title="x"))[
        "result"
    ]
    assert resultat["isError"] is True
    assert "tracker externe" in resultat["content"][0]["text"]


async def test_un_jeton_lie_a_un_projet_ne_voit_que_lui(client: AsyncClient, project: dict[str, Any]) -> None:
    autre = await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": "autre", "name": "Autre", "config": {"slug": "autre", "org": "varga"}},
    )
    assert autre.status_code == 201, autre.text
    jeton = await _jeton(client, "mcp:read", projet="varga:billing-api")
    client.cookies.clear()
    projets = (await _outil(client, jeton, "list_projects"))["result"]["structuredContent"]["projects"]
    assert [p["project"] for p in projets] == ["varga:billing-api"]
    ailleurs = await _rpc(client, jeton, "tools/list", chemin="/mcp/projects/varga:autre")
    assert ailleurs.status_code == 403, ailleurs.text


async def test_la_decision_attendue_se_prend_dans_la_console(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import HumanRequest, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="BIL-7", title="À valider", state="review")
        session.add(item)
        await session.flush()
        session.add(
            HumanRequest(
                work_item_id=item.id,
                project_id=project["id"],
                kind="approval",
                payload={"question": "Fusionner ?"},
                requested_at=utcnow(),
            )
        )
        item_id = item.id
    jeton = await _jeton(client, "mcp:read")
    client.cookies.clear()
    ticket = (await _outil(client, jeton, "get_work_item", work_item="BIL-7"))["result"]["structuredContent"]
    assert ticket["pending_decision"]["decision_url"].endswith(f"/p/billing-api/items/{item_id}")
    attentes = (await _outil(client, jeton, "list_pending_decisions"))["result"]["structuredContent"][
        "pending"
    ]
    assert [(a["key"], a["can_decide"]) for a in attentes] == [("BIL-7", True)]


# ───────────────────────────── garde-fous ─────────────────────────────


async def test_le_budget_d_ecritures_du_jour(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import AuditLog, Organization, User
    from choregos_api.db.session import session_scope
    from choregos_api.mcp.garde_fous import ECRITURES_PAR_JOUR
    from choregos_core import utcnow
    from sqlalchemy import select

    await _tracker_interne(client, project)
    async with session_scope() as session:
        admin_id = (await session.execute(select(User.id).where(User.email == ADMIN))).scalar_one()
        org_id = (
            await session.execute(select(Organization.id).where(Organization.slug == "varga"))
        ).scalar_one()
        for _ in range(ECRITURES_PAR_JOUR):
            session.add(
                AuditLog(
                    actor_id=admin_id,
                    org_id=org_id,
                    action="mcp.call",
                    target_type="mcp_tool",
                    target_id="create_work_item",
                    payload={"ecriture": True},
                    ts=utcnow(),
                )
            )
    jeton = await _jeton(client, "mcp:write")
    client.cookies.clear()
    resultat = (await _outil(client, jeton, "create_work_item", project="varga:billing-api", title="x"))[
        "result"
    ]
    assert resultat["isError"] is True
    assert "budget_exhausted" in resultat["content"][0]["text"]


async def test_le_debit_par_jeton(client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.limiteur import Limiteur
    from choregos_api.mcp import transport

    monkeypatch.setattr(transport, "LIMITEUR", Limiteur(2))
    jeton = await _jeton(client, "mcp:read")
    client.cookies.clear()
    statuts = [(await _rpc(client, jeton, "ping")).status_code for _ in range(3)]
    assert statuts == [200, 200, 429]


def test_un_resultat_trop_long_est_tronque_et_le_dit() -> None:
    from choregos_api.mcp.garde_fous import tronquer

    assert tronquer("court") == "court"
    long = tronquer("x" * 70_000, taille=60_000)
    assert long.startswith("x" * 60_000) and "result_truncated: 10000" in long


async def test_la_console_sait_ou_est_la_porte(client: AsyncClient, admin: str) -> None:
    """La page Integrations pré-remplit ses extraits avec CETTE URL : elle se déduit de l'URL publique."""
    reponse = await client.get("/api/v1/integrations")
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["mcp_url"].endswith("/mcp")
    assert "2025-06-18" in corps["protocol_versions"]
    assert corps["oauth"]["enabled"] is False
