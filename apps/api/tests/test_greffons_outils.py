"""Un greffon INSTALLÉ peut-il offrir des outils aux runs — par le chemin du catalogue, et lui seul ?

Le greffon est un vrai module avec son `.dist-info`, chargé par `create_app()` : chaque test
demande `greffon_outils` AVANT `client`, donc avant que la fixture `app` construise l'application.
Ses outils doivent arriver chez l'agent comme ceux du catalogue : annoncés par
`GET /internal/runs/{id}/tools`, appelés par `POST /internal/runs/{id}/tools/{nom}` sous le jeton du
run, sous le même plafond d'appels, avec la même ligne au registre des coûts. Un nom déjà pris
est refusé, pas masqué.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

GREFFON = """
from choregos_api.greffons import declarer_un_fournisseur_d_outils

OUTIL = {
    "name": "annuaire_interne",
    "description": "Cherche une personne dans l'annuaire du projet.",
    "inputSchema": {"type": "object", "properties": {"nom": {"type": "string"}}},
}


async def lister(session, run, projet):
    return [OUTIL]


async def appeler(session, run, projet, nom, arguments):
    return 200, {"run": run.id, "projet": projet.slug, "outil": nom, "arguments": arguments}


def brancher():
    declarer_un_fournisseur_d_outils("greffon_outils", lister, appeler)
"""

CATALOGUE_QUI_RECOUVRE = """
outils:
  - name: annuaire_interne
    description: Le même nom, côté catalogue.
    provider: annuaire
    http: { method: GET, url: https://api.annuaire.example/v1/search }
"""


@pytest.fixture
def greffon_outils(tmp_path: pathlib.Path) -> Iterator[None]:
    from choregos_api.greffons import reinitialiser

    racine = tmp_path / "site"
    racine.mkdir()
    (racine / "greffon_outils.py").write_text(GREFFON, encoding="utf-8")
    info = racine / "greffon_outils-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: greffon_outils\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        "[choregos.plugins]\ngreffon_outils = greffon_outils:brancher\n", encoding="utf-8"
    )
    sys.path.insert(0, str(racine))
    try:
        yield
    finally:
        sys.path.remove(str(racine))
        sys.modules.pop("greffon_outils", None)
        reinitialiser()


async def _run(project_id: str) -> tuple[str, dict[str, str]]:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    async with session_scope() as session:
        item = WorkItem(project_id=project_id, tracker_key="varga/x#11", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="run-greffon-1",
                work_item_id=item.id,
                project_id=project_id,
                stage_role="custom",
                status="running",
            )
        )
    jeton = mint_run_token(
        "run-greffon-1", project_slug="billing-api", work_item_key="varga/x#11", ttl_minutes=30
    )
    return "run-greffon-1", {"Authorization": f"Bearer {jeton}"}


async def test_l_outil_du_greffon_est_annonce_au_run(
    greffon_outils: None, client: AsyncClient, project: dict[str, Any]
) -> None:
    run_id, entetes = await _run(project["id"])
    reponse = await client.get(f"/api/v1/internal/runs/{run_id}/tools", headers=entetes)
    assert reponse.status_code == 200, reponse.text
    assert [t["name"] for t in reponse.json()["tools"]] == ["annuaire_interne"]


async def test_l_appel_passe_par_le_greffon_et_se_compte(
    greffon_outils: None, client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import CostLedger, Event
    from choregos_api.db.session import session_scope
    from choregos_contracts import EventType
    from sqlalchemy import select

    run_id, entetes = await _run(project["id"])
    reponse = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/annuaire_interne", headers=entetes, json={"nom": "Ada"}
    )
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["result"] == {
        "run": run_id,
        "projet": "billing-api",
        "outil": "annuaire_interne",
        "arguments": {"nom": "Ada"},
    }
    async with session_scope() as session:
        lignes = (
            (await session.execute(select(CostLedger).where(CostLedger.run_id == run_id))).scalars().all()
        )
        appels = select(Event).where(Event.type == EventType.TOOL_CALLED.value)
        evenements = (await session.execute(appels)).scalars().all()
    assert [(ligne.kind, ligne.provider, ligne.model) for ligne in lignes] == [
        ("tool", "greffon:greffon_outils", "annuaire_interne")
    ]
    assert [e.payload.get("tool") for e in evenements] == ["annuaire_interne"]


async def test_sans_jeton_de_run_l_outil_du_greffon_est_refuse(
    greffon_outils: None, client: AsyncClient, project: dict[str, Any]
) -> None:
    run_id, _ = await _run(project["id"])
    refus = await client.post(f"/api/v1/internal/runs/{run_id}/tools/annuaire_interne", json={})
    assert refus.status_code == 401, refus.text


async def test_un_nom_deja_pris_par_le_catalogue_est_refuse_et_non_masque(
    greffon_outils: None,
    client: AsyncClient,
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_api import catalogue as service_catalogue
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    fichier = tmp_path / "catalogue.yaml"
    fichier.write_text(CATALOGUE_QUI_RECOUVRE, encoding="utf-8")
    monkeypatch.setenv("CHOREGOS_TOOL_CATALOG", str(fichier))
    service_catalogue.vider_cache()
    async with session_scope() as session:
        projet = await session.get(Project, project["id"])
        assert projet is not None
        projet.config = {**(projet.config or {}), "tools": ["annuaire_interne"]}
    try:
        run_id, entetes = await _run(project["id"])
        liste = await client.get(f"/api/v1/internal/runs/{run_id}/tools", headers=entetes)
        appel = await client.post(
            f"/api/v1/internal/runs/{run_id}/tools/annuaire_interne", headers=entetes, json={}
        )
    finally:
        monkeypatch.delenv("CHOREGOS_TOOL_CATALOG")
        service_catalogue.vider_cache()
    assert liste.status_code == 409, liste.text
    assert appel.status_code == 409, appel.text
    assert "greffon_outils" in appel.text
