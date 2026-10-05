"""Le type `mcp` : un vrai serveur MCP, déclaré par l'organisation (ADR 0034, S19-03).

Ses opérations se DÉCOUVRENT, et un outil découvert naît fermé : un serveur n'ajoute pas une
capacité dans le dos de l'administrateur. Un schéma qui dérive referme l'opération. Ce qui part
vers le serveur est la clé DU CONNECTEUR, jamais celle d'un humain ni d'un run.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login

OUTILS: list[dict[str, Any]] = [
    {"name": "suivi_commande", "description": "où en est la commande", "inputSchema": {"type": "object"},
     "annotations": {"readOnlyHint": True}},
    {"name": "commander_poste", "description": "commande un PC",
     "inputSchema": {"type": "object", "properties": {"modele": {"type": "string"}}}},
    {"name": "annuler_commande", "inputSchema": {"type": "object"}},
]  # fmt: skip


@pytest.fixture
def fournisseur(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    """L'agent du fournisseur, en faux : il exige la clé du connecteur."""
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    serveur = FakeMcpServer(outils=[dict(o) for o in OUTILS], jeton="cle-fournisseur")
    monkeypatch.setattr(choregos_adapters, "FAUX_MCP", serveur)
    monkeypatch.setenv("CHOREGOS_TEST_CLE_FOURNISSEUR", "cle-fournisseur")
    yield serveur


async def _declarer(client: AsyncClient) -> None:
    corps = {"name": "fournisseur", "type": "mcp", "config": {"url": "https://fournisseur.test/mcp"},
             "secret_refs": {"token": "env:CHOREGOS_TEST_CLE_FOURNISSEUR"}}  # fmt: skip
    cree = await client.post("/api/v1/orgs/varga/connectors", json=corps)
    assert cree.status_code == 201, cree.text
    assert cree.json()["operations"] == [], "un serveur MCP ne déclare rien : il se découvre"


async def _operations(client: AsyncClient) -> dict[str, dict[str, Any]]:
    instance = (await client.get("/api/v1/orgs/varga/connectors/fournisseur")).json()
    return {o["name"]: o for o in instance["operations"]}


async def test_un_outil_decouvert_nait_ferme(
    client: AsyncClient, project: dict[str, Any], fournisseur: Any
) -> None:
    await _declarer(client)
    diff = await client.post("/api/v1/orgs/varga/connectors/fournisseur/discover")
    assert diff.status_code == 200, diff.text
    assert sorted(diff.json()["added"]) == ["annuler_commande", "commander_poste", "suivi_commande"]
    operations = await _operations(client)
    assert {n: o["policy"] for n, o in operations.items()} == dict.fromkeys(operations, "forbidden")
    assert operations["suivi_commande"]["access"] == "read"
    assert operations["commander_poste"]["access"] == "write", "sans readOnlyHint : une écriture"
    assert operations["commander_poste"]["schema_digest"].startswith("sha256:")
    vues = (await client.get(f"/api/v1/projects/{project['id']}/operations")).json()
    assert {o["effective_policy"] for o in vues} == {"forbidden"}


async def test_un_schema_qui_derive_referme_l_operation(
    client: AsyncClient, admin: str, fournisseur: Any
) -> None:
    await _declarer(client)
    await client.post("/api/v1/orgs/varga/connectors/fournisseur/discover")
    ouverte = await client.patch(
        "/api/v1/orgs/varga/connectors/fournisseur/operations/commander_poste", json={"policy": "approval"}
    )
    assert ouverte.status_code == 200
    rien = (await client.post("/api/v1/orgs/varga/connectors/fournisseur/discover")).json()
    assert rien == {"added": [], "changed": [], "removed": [], "unchanged": 3}
    assert (await _operations(client))["commander_poste"]["policy"] == "approval", "rien n'a bougé"

    # Le serveur élargit `commander_poste` (une quantité) et retire `annuler_commande`.
    fournisseur.outils[1]["inputSchema"]["properties"]["quantite"] = {"type": "integer"}
    fournisseur.outils.pop(2)
    diff = (await client.post("/api/v1/orgs/varga/connectors/fournisseur/discover")).json()
    assert diff == {
        "added": [],
        "changed": ["commander_poste"],
        "removed": ["annuler_commande"],
        "unchanged": 1,
    }
    operations = await _operations(client)
    assert operations["commander_poste"]["policy"] == "forbidden", "refermée par la dérive"
    assert "annuler_commande" not in operations


async def test_seule_la_cle_du_connecteur_part_vers_le_serveur(
    client: AsyncClient, admin: str, fournisseur: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _declarer(client)
    assert (await client.post("/api/v1/orgs/varga/connectors/fournisseur/discover")).status_code == 200
    for methode, entetes, _ in fournisseur.recues:
        assert entetes["authorization"] == "Bearer cle-fournisseur", methode
        assert "cookie" not in entetes and "chg_" not in str(entetes), methode

    monkeypatch.delenv("CHOREGOS_TEST_CLE_FOURNISSEUR")
    panne = await client.post("/api/v1/orgs/varga/connectors/fournisseur/discover")
    assert panne.status_code == 502 and "CHOREGOS_TEST_CLE_FOURNISSEUR" in panne.text
    assert (await client.get("/api/v1/orgs/varga/connectors/fournisseur")).json()["status"] == "error"


async def test_decouvrir_est_un_geste_d_administrateur(
    client: AsyncClient, admin: str, fournisseur: Any
) -> None:
    await _declarer(client)
    membre = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "po@varga.dev", "role": "project_owner"}
    )
    assert membre.status_code in {200, 201}
    await login(client, "po@varga.dev")
    assert (await client.post("/api/v1/orgs/varga/connectors/fournisseur/discover")).status_code == 403
