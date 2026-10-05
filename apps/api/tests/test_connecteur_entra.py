"""Le connecteur `entra` dans l'organisation (S20-03) : ses opérations naissent déclarées, avec leur
schéma ; une lecture est permise, une écriture passe par une validation ; un run atteint ce que la
politique ouvre, par le courtier, et un compte hors de l'unité administrative est refusé, nommé.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"


@pytest.fixture
def graph(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.entra import FakeEntra

    faux = FakeEntra()
    monkeypatch.setattr(choregos_adapters, "FAUX_ENTRA", faux)
    monkeypatch.setenv("ENTRA_CLIENT_SECRET", "secret-du-connecteur")
    yield faux


async def _declarer(client: AsyncClient) -> dict[str, Any]:
    corps = {"name": "entra-acme", "type": "entra", "config": {"tenant_id": "acme", "client_id": "choregos"},
             "secret_refs": {"client_secret": "env:ENTRA_CLIENT_SECRET"}}  # fmt: skip
    cree = await client.post(f"{ORG}/connectors", json=corps)
    assert cree.status_code == 201, cree.text
    return {o["name"]: o for o in cree.json()["operations"]}


async def _run(client: AsyncClient, project: dict[str, Any], motifs: list[str]) -> tuple[str, dict[str, str]]:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    spec = {"mcp_servers": [{"connector": "entra-acme", "tools": motifs}]}
    cree = await client.post(
        f"{ORG}/agents", json={"slug": "coordinateur", "display_name": "Coordinateur", "spec": spec}
    )
    assert cree.status_code == 201, cree.text
    async with session_scope() as session:
        item = WorkItem(
            project_id=project["id"], tracker_key="varga/rh#1", title="Arrivée de Léa", state="plan"
        )
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="run-entra-1",
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="plan",
                transition_id="t-plan",
                status="running",
                agent_slug="coordinateur",
                agent_version=1,
            )
        )
    jeton = mint_run_token(
        "run-entra-1", project_slug="billing-api", work_item_key="varga/rh#1", ttl_minutes=30
    )
    return "/api/v1/internal/runs/run-entra-1/tools", {"Authorization": f"Bearer {jeton}"}


async def test_les_operations_d_un_annuaire_naissent_declarees_lecture_permise_ecriture_validee(
    client: AsyncClient, admin: str, graph: Any
) -> None:
    operations = await _declarer(client)
    assert {n: o["policy"] for n, o in operations.items()} == {
        "add_to_group": "approval",
        "create_user": "approval",
        "disable_user": "approval",
        "get_user": "allowed",
        "remove_from_group": "approval",
        "revoke_sessions": "approval",
    }
    assert operations["create_user"]["input_schema"]["required"] == ["upn", "display_name"]


async def test_un_run_lit_l_annuaire_par_le_courtier_et_n_y_ecrit_pas_sans_validation(
    client: AsyncClient, project: dict[str, Any], graph: Any
) -> None:
    graph.ajouter_compte("lea@acme.test")
    await _declarer(client)
    base, entetes = await _run(client, project, ["*"])
    noms = [o["name"] for o in (await client.get(base, headers=entetes)).json()["tools"]]
    assert noms == ["entra-acme__get_user"], (
        "les écritures sont sous validation : des actions, pas des outils"
    )

    lu = await client.post(f"{base}/entra-acme__get_user", headers=entetes, json={"upn": "lea@acme.test"})
    assert lu.status_code == 200 and lu.json()["status_code"] == 200, lu.text
    assert lu.json()["result"]["structuredContent"]["user"]["userPrincipalName"] == "lea@acme.test"
    assert (await client.post(f"{base}/entra-acme__get_user", headers=entetes, json={})).status_code == 400
    assert (await client.post(f"{base}/entra-acme__create_user", headers=entetes, json={})).status_code == 404


async def test_un_compte_hors_de_l_unite_est_refuse_meme_ouvert(
    client: AsyncClient, project: dict[str, Any], graph: Any
) -> None:
    graph.ajouter_compte("pdg@acme.test", dans_l_unite=False)
    await _declarer(client)
    ouverte = await client.patch(
        f"{ORG}/connectors/entra-acme/operations/disable_user", json={"policy": "allowed"}
    )
    assert ouverte.status_code == 200
    base, entetes = await _run(client, project, ["disable_*"])
    refus = (
        await client.post(f"{base}/entra-acme__disable_user", headers=entetes, json={"upn": "pdg@acme.test"})
    ).json()
    assert refus["status_code"] == 422 and "unité administrative" in refus["result"]["error"]
    assert all(c["accountEnabled"] for c in graph.comptes.values()), "rien n'a bougé dans l'annuaire"
