# SPDX-License-Identifier: Apache-2.0
"""L'ontologie servie à la porte MCP des clients externes, avec les droits de l'humain (ADR 0030).

Le client MCP d'une personne — Claude Code, Cursor… — voit l'ontologie du projet sur la porte du
projet (`/mcp/projects/{org}:{slug}`). Une action s'y propose si l'un des rôles de la PERSONNE figure
dans `permissions.propose`, pas parce que la plateforme le permet à son agent ; la proposition porte
son nom, et elle attend une décision qui se prend dans la console — jamais par la porte.
"""

from __future__ import annotations

import pathlib
from typing import Any

from httpx import AsyncClient

from .aides import connecter, paquet

PORTE = "/mcp/projects/varga:infra"
PARAMS = {"key": "disk-full", "source": "healthcheck", "check": "disk-usage", "severity": "low"}


async def _jeton(http: AsyncClient, portee: str = "mcp:write") -> str:
    cree = await http.post("/api/v1/me/tokens", json={"name": "claude-code", "scopes": [portee]})
    assert cree.status_code == 201, cree.text
    return str(cree.json()["token"])


async def _rpc(http: AsyncClient, jeton: str, methode: str, params: dict[str, Any] | None = None) -> Any:
    reponse = await http.post(
        PORTE,
        json={"jsonrpc": "2.0", "id": 1, "method": methode, **({"params": params} if params else {})},
        headers={"Authorization": f"Bearer {jeton}"},
    )
    assert reponse.status_code == 200, reponse.text
    return reponse.json()


async def _ontologie(http: AsyncClient, projet: dict[str, Any], racine: pathlib.Path) -> None:
    reponse = await http.put(f"/api/v1/projects/{projet['id']}/ontology", json={"files": paquet(racine)})
    assert reponse.status_code == 200, reponse.text


async def _membre(http: AsyncClient, email: str, role: str) -> None:
    reponse = await http.post("/api/v1/orgs/varga/members", json={"email": email, "role": role})
    assert reponse.status_code in {200, 201}, reponse.text


async def test_une_action_reservee_au_proprietaire_n_est_pas_annoncee_a_un_developpeur(
    client: AsyncClient, projet: dict[str, Any], core_ref: pathlib.Path, mutate: Any
) -> None:
    avant, apres = "propose: [role:contributor, system]", "propose: [role:owner, system]"
    mutate("actions/open_finding.yaml", avant, apres)
    await _ontologie(client, projet, core_ref)
    await _membre(client, "dev@varga.dev", "developer")
    await connecter(client, "dev@varga.dev")
    jeton = await _jeton(client)
    client.cookies.clear()

    noms = {o["name"] for o in (await _rpc(client, jeton, "tools/list"))["result"]["tools"]}
    assert "finding_search" in noms, "les lectures de l'ontologie passent la porte"
    assert "action_open_finding" not in noms
    appel = await _rpc(client, jeton, "tools/call", {"name": "action_open_finding", "arguments": {}})
    assert appel["error"]["code"] == -32602, "hors des droits : comme un outil inconnu"


async def test_un_jeton_de_lecture_ne_voit_aucune_action(
    client: AsyncClient, projet: dict[str, Any], core_ref: pathlib.Path
) -> None:
    await _ontologie(client, projet, core_ref)
    jeton = await _jeton(client, "mcp:read")
    client.cookies.clear()
    noms = {o["name"] for o in (await _rpc(client, jeton, "tools/list"))["result"]["tools"]}
    assert "finding_search" in noms
    assert not {n for n in noms if n.startswith("action_") and n not in {"action_status", "action_list"}}


async def test_la_proposition_porte_le_nom_de_la_personne_et_attend_dans_la_console(
    client: AsyncClient, projet: dict[str, Any], core_ref: pathlib.Path, mutate: Any
) -> None:
    # Plus rien n'est approuvé d'office : la proposition attend un propriétaire.
    mutate("policies/core_ref.yaml", "when: \"action.risk == 'low'\"", 'when: "false"')
    await _ontologie(client, projet, core_ref)
    jeton = await _jeton(client)
    client.cookies.clear()

    appel = await _rpc(
        client,
        jeton,
        "tools/call",
        {"name": "action_open_finding", "arguments": {"params": PARAMS, "justification": "disque plein"}},
    )
    resultat = appel["result"]
    assert resultat["isError"] is False, resultat
    proposition = resultat["structuredContent"]["proposal"]

    attentes = (await _rpc(client, jeton, "tools/call", {"name": "list_pending_decisions"}))["result"]
    (attente,) = [a for a in attentes["structuredContent"]["pending"] if a["key"] == proposition]
    assert attente["kind"] == "action proposal"
    assert attente["decision_url"].endswith(f"/p/infra/proposals/{proposition}")
    assert attente["can_decide"] is False, "qui propose ne décide pas (séparation des rôles)"

    await connecter(client, "admin@varga.dev")
    lue = await client.get(f"/api/v1/projects/{projet['id']}/proposals/{proposition}")
    assert lue.status_code == 200, lue.text
    assert lue.json()["proposed_by"] == {"kind": "user", "id": "admin@varga.dev", "via": "mcp"}
    assert lue.json()["status"] == "pending_approval"
