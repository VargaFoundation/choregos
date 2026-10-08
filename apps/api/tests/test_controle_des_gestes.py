# SPDX-License-Identifier: Apache-2.0
"""Un greffon INSTALLÉ peut-il refuser un geste humain — un projet de trop, une approbation sans
authentification fraîche — sans que la route agisse ?

Le greffon est un vrai module avec son `.dist-info`, chargé par `charger_les_greffons()`.
"""

from __future__ import annotations

import pathlib
import sys
import time
from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

GREFFON = """
import time

from choregos_api.greffons import GesteRefuse, declarer_un_controle_de_geste

ETAT = {"projets_max": None, "fraicheur_s": None, "vues": []}


def plafond_de_projets(session, demande):
    ETAT["vues"].append(demande)
    if ETAT["projets_max"] is not None and ETAT["projets_max"] <= 1:
        raise GesteRefuse(f"{demande.org} a atteint son plafond de projets", "conflit")


def authentification_fraiche(session, demande):
    ETAT["vues"].append(demande)
    fraicheur = ETAT["fraicheur_s"]
    if fraicheur is None:
        return
    quand = demande.principal.authentifie_le
    if quand is None or time.time() - quand > fraicheur:
        raise GesteRefuse("approuver demande une authentification de moins de 5 minutes", "reauth")


def brancher():
    declarer_un_controle_de_geste("project.create", "plafond", plafond_de_projets)
    declarer_un_controle_de_geste("workitem.decision", "fraicheur", authentification_fraiche)
    declarer_un_controle_de_geste("release.approve", "fraicheur", authentification_fraiche)
"""


@pytest.fixture
def greffon(tmp_path: pathlib.Path) -> Iterator[dict[str, Any]]:
    from choregos_adapters import charger_les_greffons
    from choregos_api.greffons import reinitialiser

    racine = tmp_path / "site"
    racine.mkdir()
    (racine / "greffon_gestes.py").write_text(GREFFON, encoding="utf-8")
    info = racine / "greffon_gestes-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: greffon_gestes\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        "[choregos.plugins]\ngreffon_gestes = greffon_gestes:brancher\n", encoding="utf-8"
    )
    sys.path.insert(0, str(racine))
    try:
        assert "greffon_gestes" in charger_les_greffons()
        yield sys.modules["greffon_gestes"].ETAT
    finally:
        sys.path.remove(str(racine))
        sys.modules.pop("greffon_gestes", None)
        reinitialiser()


def _projet(slug: str) -> dict[str, Any]:
    return {
        "slug": slug,
        "name": slug,
        "config": {
            "slug": slug,
            "org": "varga",
            "repo": {
                "url": f"https://github.com/varga/{slug}.git",
                "default_branch": "main",
                "language": "python",
            },
        },
    }


async def _compter_projets() -> int:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        return int((await session.execute(select(func.count()).select_from(Project))).scalar_one())


async def test_un_projet_de_trop_est_refuse_avant_d_etre_cree(
    client: AsyncClient, project: dict[str, Any], greffon: dict[str, Any]
) -> None:
    greffon["projets_max"] = 1
    avant = await _compter_projets()
    refus = await client.post("/api/v1/orgs/varga/projects", json=_projet("second"))
    assert refus.status_code == 409, refus.text
    assert "plafond: varga a atteint son plafond de projets" in refus.text
    assert await _compter_projets() == avant
    (demande,) = greffon["vues"]
    assert (demande.geste, demande.org, demande.cible) == ("project.create", "varga", {"slug": "second"})

    greffon["projets_max"] = None
    assert (await client.post("/api/v1/orgs/varga/projects", json=_projet("second"))).status_code == 201


async def _demande_humaine(client: AsyncClient, project: dict[str, Any]) -> str:
    from choregos_api.db.base import utcnow
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope

    await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    item = (
        await client.post(f"/api/v1/projects/{project['id']}/work-items", json={"title": "T", "size": "S"})
    ).json()
    async with session_scope(orgs="*") as session:
        session.add(
            HumanRequest(
                work_item_id=item["id"],
                project_id=project["id"],
                transition_id="t-prod",
                kind="approval",
                payload={"env": "prod"},
                requested_at=utcnow(),
            )
        )
    return str(item["id"])


async def test_une_approbation_sans_authentification_fraiche_est_refusee_en_401(
    client: AsyncClient, project: dict[str, Any], greffon: dict[str, Any]
) -> None:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope

    item = await _demande_humaine(client, project)
    greffon["fraicheur_s"] = -1  # aucune session n'est assez fraîche
    refus = await client.post(f"/api/v1/work-items/{item}/decisions", json={"kind": "approve"})
    assert refus.status_code == 401, refus.text
    assert "fraicheur: approuver demande une authentification de moins de 5 minutes" in refus.text
    assert "auth/login?reauth=1" in refus.text
    async with session_scope(orgs="*") as session:
        (demande,) = (await session.execute(select(HumanRequest))).scalars()
        assert demande.decided_at is None, "la décision a été enregistrée malgré le refus"
    vue = greffon["vues"][-1]
    assert vue.cible["transition_id"] == "t-prod" and vue.cible["request_payload"] == {"env": "prod"}
    # La session vient d'être ouverte : son `iat` est à quelques secondes.
    assert vue.principal.authentifie_le is not None and time.time() - vue.principal.authentifie_le < 60

    greffon["fraicheur_s"] = 300
    ok = await client.post(f"/api/v1/work-items/{item}/decisions", json={"kind": "approve"})
    assert ok.status_code == 202, ok.text


async def test_l_approbation_d_une_release_passe_par_le_meme_controle(
    client: AsyncClient, project: dict[str, Any], greffon: dict[str, Any]
) -> None:
    from choregos_api.db.models import Release
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        release = Release(project_id=project["id"], env="prod", status="awaiting_approval", items=[])
        session.add(release)
        await session.flush()
        release_id = str(release.id)
    greffon["fraicheur_s"] = -1  # plus aucune session n'est assez fraîche
    refus = await client.post(f"/api/v1/releases/{release_id}/approve", json={"note": "go"})
    assert refus.status_code == 401, refus.text
    assert greffon["vues"][-1].cible == {"project": "billing-api", "release": release_id, "env": "prod"}
    async with session_scope(orgs="*") as session:
        assert (await session.get(Release, release_id)).approved_by is None  # type: ignore[union-attr]


async def test_un_geste_inconnu_est_refuse_a_la_declaration() -> None:
    from choregos_api.greffons import declarer_un_controle_de_geste

    with pytest.raises(ValueError, match="geste inconnu"):
        declarer_un_controle_de_geste("project.delete", "x", lambda s, d: None)
