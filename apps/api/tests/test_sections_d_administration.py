"""Les sections d'administration déclarées par un greffon (ADR 0032, S17-01).

Un greffon — l'édition entreprise — sert ses routes d'administration ; il déclare ses écrans comme
des DONNÉES, et la console les rend. Le cœur vérifie au démarrage que chaque section est bien
formée et que chacun de ses chemins est servi, et ne montre une section qu'à qui a son droit.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

import choregos_contracts as contracts
import pytest
from fastapi import APIRouter
from httpx import AsyncClient

from .conftest import login

SCIM: dict[str, Any] = json.loads(
    (contracts.schemas_dir() / "examples" / "ui-manifest.example.json").read_text()
)


def _routeur_scim() -> APIRouter:
    """Les routes que le manifeste d'exemple nomme, servies par le « greffon »."""
    routeur = APIRouter()

    @routeur.get("/orgs/{org}/scim/settings")
    async def lire(org: str) -> dict[str, Any]:
        return {"enabled": False}

    @routeur.put("/orgs/{org}/scim/settings")
    async def ecrire(org: str) -> dict[str, Any]:
        return {"enabled": True}

    @routeur.get("/orgs/{org}/scim/tokens")
    async def lister(org: str) -> dict[str, Any]:
        return {"items": []}

    @routeur.post("/orgs/{org}/scim/tokens")
    async def creer(org: str) -> dict[str, Any]:
        return {"id": "t1", "token": "scim_secret"}

    @routeur.delete("/orgs/{org}/scim/tokens/{token_id}")
    async def revoquer(org: str, token_id: str) -> dict[str, Any]:
        return {}

    return routeur


@pytest.fixture
def greffon() -> Iterator[Any]:
    from choregos_api import greffons

    greffons.declarer_un_routeur(_routeur_scim())
    greffons.declarer_une_section_d_administration(SCIM)
    plateforme = {
        "id": "organisations",
        "title": "organisations",
        "scope": "platform",
        "permission": "platform:admin",
        "blocks": [
            {
                "kind": "table",
                "title": "organisations",
                "list": "/orgs",
                "columns": [{"key": "slug", "label": "slug"}],
            }
        ],
    }
    greffons.declarer_une_section_d_administration(plateforme)
    yield greffons
    greffons.reinitialiser()


async def test_l_administrateur_voit_les_sections_le_developpeur_non(
    greffon: Any, client: AsyncClient, admin: str
) -> None:
    sections = (await client.get("/api/v1/ui/admin-sections")).json()
    assert [s["id"] for s in sections] == ["scim", "organisations"]
    assert sections[0]["blocks"][2]["secret_field"] == "token"

    membre = await client.post(
        "/api/v1/orgs/varga/members", json={"email": "dev@varga.dev", "role": "developer"}
    )
    assert membre.status_code in {200, 201}, membre.text
    await login(client, "dev@varga.dev")
    assert (await client.get("/api/v1/ui/admin-sections")).json() == [], "ni member:manage ni la plateforme"


@pytest.mark.parametrize(
    ("alteration", "motif"),
    [
        pytest.param(lambda m: m.pop("blocks"), "invalide", id="manifeste-invalide"),
        pytest.param(
            lambda m: m.update(permission="org:everything"), "permission inconnue", id="permission-inconnue"
        ),
        pytest.param(
            lambda m: m["blocks"][1].update(list="/orgs/{org}/scim/jetons"),
            "aucune route",
            id="chemin-non-servi",
        ),
        pytest.param(
            lambda m: m["blocks"][1]["row_actions"][0].update(method="POST"),
            "aucune route",
            id="methode-non-servie",
        ),
    ],
)
def test_une_section_qui_ne_tient_pas_arrete_le_demarrage(alteration: Any, motif: str) -> None:
    from choregos_api import greffons
    from choregos_api.main import create_app

    fautive = copy.deepcopy(SCIM)
    alteration(fautive)
    greffons.declarer_un_routeur(_routeur_scim())
    greffons.declarer_une_section_d_administration(fautive)
    try:
        with pytest.raises(RuntimeError, match=motif):
            create_app()
    finally:
        greffons.reinitialiser()
