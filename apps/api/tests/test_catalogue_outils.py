"""Catalogue d'outils : l'agent appelle par son jeton de run, la clé reste ici.

La démonstration RH du 2026-09-23 s'est arrêtée sur « aucune base de profils accessible ».
Le réflexe aurait été de poser une clé d'API dans le pod de l'agent ; ces tests vérifient
que la plateforme fait l'appel à sa place, le plafonne et le compte.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


def _intercepter(monkeypatch: pytest.MonkeyPatch, reponse: httpx.Response, vues: list[Any]) -> None:
    """Remplace l'appel au FOURNISSEUR, et lui seul.

    Le client de test est lui aussi un `httpx.AsyncClient` : patcher `request` sans
    distinguer l'hôte détournait les appels du test vers l'API elle-même, qui répondait
    alors la réponse du fournisseur. Le premier symptôme était un `KeyError: 'tools'` sur
    une réponse 200 — un mensonge parfait.
    """
    original = httpx.AsyncClient.request

    async def faux(self: Any, *args: Any, **kwargs: Any) -> httpx.Response:
        url = str(args[1]) if len(args) > 1 else str(kwargs.get("url", ""))
        if not url.startswith("https://api.annuaire.example"):
            return await original(self, *args, **kwargs)  # type: ignore[no-any-return]
        vues.append(httpx.Request(args[0], url, params=kwargs.get("params"), headers=kwargs.get("headers")))
        return reponse

    monkeypatch.setattr(httpx.AsyncClient, "request", faux)


CATALOGUE = """
outils:
  - name: recherche_profils
    description: Cherche des profils.
    provider: annuaire
    input_schema: { type: object, required: [metier], properties: { metier: { type: string } } }
    http:
      method: GET
      url: https://api.annuaire.example/v1/search
      query: { q: "{{ metier }}" }
      headers: { Authorization: "Bearer {{ credential }}" }
    credential_env: ANNUAIRE_API_KEY
    price_eur: 0.02
"""


async def _run_avec_catalogue(
    project: dict[str, Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outils: str = "recherche_profils",
) -> tuple[str, dict[str, str]]:
    from choregos_api import catalogue as service_catalogue
    from choregos_api.db.models import Project, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    fichier = tmp_path / "catalogue.yaml"
    fichier.write_text(CATALOGUE, encoding="utf-8")
    monkeypatch.setenv("CHOREGOS_TOOL_CATALOG", str(fichier))
    monkeypatch.setenv("ANNUAIRE_API_KEY", "cle-du-coffre")
    service_catalogue.vider_cache()

    async with session_scope() as session:
        row = await session.get(Project, project["id"])
        assert row is not None
        config = dict(row.config or {})
        config["labels"] = {**(config.get("labels") or {}), "tools": outils}
        row.config = config
        item = WorkItem(project_id=project["id"], tracker_key="varga/x#9", title="T", state="ready")
        session.add(item)
        await session.flush()
        run = Run(
            id="run-outils-1",
            work_item_id=item.id,
            project_id=project["id"],
            stage_role="custom",
            transition_id="t-sourcing",
            status="running",
        )
        session.add(run)

    jeton = mint_run_token(
        "run-outils-1", project_slug="billing-api", work_item_key="varga/x#9", ttl_minutes=30
    )
    return "run-outils-1", {"Authorization": f"Bearer {jeton}"}


async def test_l_agent_appelle_un_outil_sans_jamais_voir_la_cle(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id, headers = await _run_avec_catalogue(project, tmp_path, monkeypatch)
    vues: list[Any] = []
    _intercepter(
        monkeypatch,
        httpx.Response(
            200,
            json={"profils": [{"nom": "A"}, {"nom": "B"}]},
            request=httpx.Request("GET", "https://api.annuaire.example/v1/search"),
        ),
        vues,
    )

    liste = await client.get(f"/api/v1/internal/runs/{run_id}/tools", headers=headers)
    assert liste.status_code == 200, liste.text
    assert [t["name"] for t in liste.json()["tools"]] == ["recherche_profils"]

    reponse = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils",
        headers=headers,
        json={"metier": "data engineer"},
    )
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["result"]["profils"][0]["nom"] == "A"
    # La clé est partie chez le fournisseur, et nulle part ailleurs.
    assert vues[0].headers["Authorization"] == "Bearer cle-du-coffre"
    assert "cle-du-coffre" not in reponse.text


async def test_l_appel_est_inscrit_au_registre_de_couts(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un outil du catalogue coûte à chaque appel. Non compté, il serait la seule dépense
    de la plateforme que personne ne voit."""
    from choregos_api.db.models import CostLedger
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    run_id, headers = await _run_avec_catalogue(project, tmp_path, monkeypatch)

    _intercepter(
        monkeypatch,
        httpx.Response(
            200, json={"profils": []}, request=httpx.Request("GET", "https://api.annuaire.example/v1/search")
        ),
        [],
    )
    await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils", headers=headers, json={"metier": "x"}
    )

    async with session_scope() as session:
        lignes = (
            (await session.execute(select(CostLedger).where(CostLedger.run_id == run_id))).scalars().all()
        )
    assert len(lignes) == 1
    assert lignes[0].kind == "tool"
    assert lignes[0].provider == "annuaire"
    assert lignes[0].cost_eur == pytest.approx(0.02)


async def test_un_outil_non_declare_par_le_projet_est_introuvable(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Et il rend 404, pas 403 : un agent n'a pas à découvrir le catalogue du déploiement
    en essayant des noms."""
    run_id, headers = await _run_avec_catalogue(project, tmp_path, monkeypatch, outils="")
    assert (await client.get(f"/api/v1/internal/runs/{run_id}/tools", headers=headers)).json()["tools"] == []
    refus = await client.post(
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils", headers=headers, json={"metier": "x"}
    )
    assert refus.status_code == 404


async def test_le_plafond_d_appels_borne_la_facture(
    client: AsyncClient, project: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un outil payant sans plafond, c'est une facture sans plafond : une boucle qui
    s'emballe coûte autant qu'elle tourne."""
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    run_id, headers = await _run_avec_catalogue(project, tmp_path, monkeypatch)
    _intercepter(
        monkeypatch,
        httpx.Response(
            200, json={"profils": []}, request=httpx.Request("GET", "https://api.annuaire.example/v1/search")
        ),
        [],
    )

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
        document["budgets"] = {**(document.get("budgets") or {}), "tool_calls_per_run": 2}
        politique.json_doc = document

    appel = lambda: client.post(  # noqa: E731
        f"/api/v1/internal/runs/{run_id}/tools/recherche_profils", headers=headers, json={"metier": "x"}
    )
    assert (await appel()).json()["remaining"] == 1
    assert (await appel()).json()["remaining"] == 0
    trop = await appel()
    assert trop.status_code == 429, trop.text
