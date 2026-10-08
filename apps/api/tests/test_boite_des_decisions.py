"""Un outil sous validation devient une action ; la boîte des décisions (ADR 0035, S20-02).

L'agent appelle `fournisseur__commander_poste` : rien n'atteint le serveur, une action est
proposée en son nom (`agent:<slug>`), et elle attend dans la boîte de l'organisation. Le
propriétaire de l'agent ne l'approuve pas ; une autre personne, ré-authentifiée, si.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import login
from .test_courtier import OUTILS, _preparer


@pytest.fixture
def fournisseur(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer

    serveur = FakeMcpServer(outils=[dict(o) for o in OUTILS], jeton="cle-fournisseur")
    monkeypatch.setattr(choregos_adapters, "FAUX_MCP", serveur)
    monkeypatch.setenv("CHOREGOS_TEST_CLE_FOURNISSEUR", "cle-fournisseur")
    yield serveur


async def test_un_outil_sous_validation_propose_une_action_et_rien_n_atteint_le_serveur(
    client: AsyncClient, project: dict[str, Any], fournisseur: Any, app: Any
) -> None:
    from choregos_api.temporal import get_temporal

    base, entetes = await _preparer(client, project, ["*"])
    appel = await client.post(
        f"{base}/fournisseur__commander_poste", headers=entetes, json={"modele": "portable-14"}
    )
    assert appel.status_code == 200 and appel.json()["status_code"] == 202, appel.text
    action_id = appel.json()["result"]["action"]
    assert appel.json()["result"]["decision_path"] == f"/p/billing-api/actions/{action_id}"
    assert fournisseur.appels == [], "rien n'atteint le serveur avant la décision"

    boite = (await client.get("/api/v1/orgs/varga/actions", params={"status": "pending_approval"})).json()
    (attente,) = [a for a in boite if a["id"] == action_id]
    assert attente["origin"] == "tool"
    assert attente["proposed_by"]["id"] == "agent:coordinateur"
    assert attente["effects"][0]["with"] == {
        "connector": "fournisseur",
        "operation": "commander_poste",
        "arguments": {"modele": "portable-14"},
    }

    # Le propriétaire de l'agent (qui l'a créé) n'approuve pas les écritures de son agent.
    base_decision = f"/api/v1/projects/{project['id']}/actions/{action_id}/decision"
    refus = await client.post(base_decision, json={"decision": "approve"})
    assert refus.status_code == 422 and "separation of duties" in refus.text

    membre = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "rh@varga.dev", "role": "project_owner"}
    )
    assert membre.status_code in {200, 201}
    rh = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    try:
        await login(rh, "rh@varga.dev")
        approuvee = await rh.post(base_decision, json={"decision": "approve"})
    finally:
        await rh.aclose()
    assert approuvee.status_code == 200 and approuvee.json()["status"] == "approved"
    assert f"action-{action_id}" in get_temporal().started  # type: ignore[attr-defined]
    assert fournisseur.appels == [], "même approuvée : c'est Temporal qui l'exécute, pas la requête"


async def test_une_proposition_compte_dans_le_plafond_du_run(
    client: AsyncClient, project: dict[str, Any], fournisseur: Any
) -> None:
    """Une boucle d'agent ne remplit pas la boîte : le plafond d'appels du run tient aussi."""
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    base, entetes = await _preparer(client, project, ["*"])
    async with session_scope() as session:
        politique = (
            (
                await session.execute(
                    select(PolicyDef)
                    .where(PolicyDef.project_id == project["id"])
                    .order_by(PolicyDef.version.desc())
                )
            )
            .scalars()
            .first()
        )
        assert politique is not None
        document = dict(politique.json_doc)
        document["budgets"] = {**(document.get("budgets") or {}), "tool_calls_per_run": 1}
        politique.json_doc = document
    assert (
        await client.post(f"{base}/fournisseur__commander_poste", headers=entetes, json={})
    ).status_code == 200
    assert (
        await client.post(f"{base}/fournisseur__commander_poste", headers=entetes, json={})
    ).status_code == 429
