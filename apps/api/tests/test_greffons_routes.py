# SPDX-License-Identifier: Apache-2.0
"""Un greffon INSTALLÉ peut-il servir ses propres routes — et seulement AJOUTER ?

Le greffon est un vrai module avec son `.dist-info` sur disque, trouvé par `importlib.metadata`
comme au démarrage : c'est `create_app()` qui le charge et inclut son routeur, pas le test.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

GREFFON = '''
from fastapi import APIRouter

from choregos_api.deps import Me
from choregos_api.greffons import declarer_un_routeur

routeur = APIRouter(tags=["greffon"])


@routeur.get("/greffon/ping")
async def ping() -> dict[str, str]:
    return {"pong": "greffon"}


@routeur.get("/greffon/moi")
async def moi(principal: Me) -> dict[str, str]:
    """Les dépendances du cœur servent aux routes d'un greffon comme aux siennes."""
    return {"email": principal.email}


def brancher():
    declarer_un_routeur(routeur)
'''

#: Recouvre `GET /api/v1/orgs` du cœur.
GREFFON_QUI_RECOUVRE = """
from fastapi import APIRouter

from choregos_api.greffons import declarer_un_routeur

routeur = APIRouter()


@routeur.get("/orgs")
async def toutes_les_orgs() -> list[str]:
    return ["tout", "le", "monde"]


def brancher():
    declarer_un_routeur(routeur)
"""


def _installer(racine: pathlib.Path, nom: str, source: str) -> None:
    """Écrit un module et le `.dist-info` qui le déclare — comme le ferait `pip install`."""
    (racine / f"{nom}.py").write_text(source, encoding="utf-8")
    info = racine / f"{nom}-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {nom}\nVersion: 0.1.0\n", encoding="utf-8")
    (info / "entry_points.txt").write_text(f"[choregos.plugins]\n{nom} = {nom}:brancher\n", encoding="utf-8")


def _greffon(tmp_path: pathlib.Path, nom: str, source: str) -> Iterator[None]:
    from choregos_api.greffons import reinitialiser

    racine = tmp_path / nom
    racine.mkdir()
    _installer(racine, nom, source)
    sys.path.insert(0, str(racine))
    try:
        yield
    finally:
        sys.path.remove(str(racine))
        sys.modules.pop(nom, None)
        reinitialiser()


@pytest.fixture
def greffon_a_routes(tmp_path: pathlib.Path) -> Iterator[None]:
    yield from _greffon(tmp_path, "greffon_a_routes", GREFFON)


@pytest.fixture
def greffon_qui_recouvre(tmp_path: pathlib.Path) -> Iterator[None]:
    yield from _greffon(tmp_path, "greffon_qui_recouvre", GREFFON_QUI_RECOUVRE)


@pytest.fixture
async def client_avec_greffon(greffon_a_routes: None, tmp_path: pathlib.Path) -> AsyncIterator[AsyncClient]:
    """L'application construite APRÈS l'installation du greffon, comme au démarrage d'un pod."""
    import os

    os.environ["CHOREGOS_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/greffon.db"
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.db.session import create_all
    from choregos_api.main import create_app

    reset_settings_cache()
    await db_session.dispose_engine()
    application = create_app()
    await create_all()
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as http:
        yield http
    await db_session.dispose_engine()
    reset_settings_cache()


async def test_le_greffon_sert_sa_route(client_avec_greffon: AsyncClient) -> None:
    reponse = await client_avec_greffon.get("/api/v1/greffon/ping")
    assert reponse.status_code == 200, reponse.text
    assert reponse.json() == {"pong": "greffon"}


async def test_la_route_du_greffon_est_gardee_par_l_authentification_du_coeur(
    client_avec_greffon: AsyncClient,
) -> None:
    anonyme = await client_avec_greffon.get("/api/v1/greffon/moi")
    assert anonyme.status_code == 401, anonyme.text

    depart = await client_avec_greffon.get("/api/v1/auth/login", params={"as": "admin@varga.dev"})
    await client_avec_greffon.get(depart.headers["location"])
    connecte = await client_avec_greffon.get("/api/v1/greffon/moi")
    assert connecte.status_code == 200, connecte.text
    assert connecte.json() == {"email": "admin@varga.dev"}


async def test_la_route_du_greffon_figure_dans_l_openapi(client_avec_greffon: AsyncClient) -> None:
    """Un exploitant doit pouvoir voir ce qu'un greffon ajoute à la surface de l'API."""
    chemins = (await client_avec_greffon.get("/openapi.json")).json()["paths"]
    assert "/api/v1/greffon/ping" in chemins


def test_un_greffon_qui_recouvre_une_route_du_coeur_arrete_le_demarrage(greffon_qui_recouvre: None) -> None:
    from choregos_api.main import create_app

    with pytest.raises(RuntimeError, match=r"déjà servies : GET /api/v1/orgs"):
        create_app()


def test_sans_greffon_aucune_route_ajoutee() -> None:
    """Le témoin : la route n'existe pas d'elle-même dans le cœur.

    Lu dans l'OpenAPI et non dans `app.routes`, qui ne montre plus les routes incluses depuis
    FastAPI 0.141 — ce témoin-là aurait été vert quoi qu'il arrive.
    """
    from choregos_api.main import create_app

    chemins: Any = create_app().openapi()["paths"]
    assert "/api/v1/orgs" in chemins, "le témoin ne verrait rien : l'OpenAPI est vide"
    assert "/api/v1/greffon/ping" not in chemins
