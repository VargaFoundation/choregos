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


def test_le_meme_greffon_charge_deux_fois_ne_declare_qu_une_section() -> None:
    """L'orchestrateur que l'API importe charge les greffons, puis `create_app()` les recharge : la
    même section revient, et ne doit pas arrêter le démarrage. Une AUTRE sous le même nom, si."""
    from choregos_api import greffons

    try:
        greffons.declarer_une_section_d_administration(SCIM)
        greffons.declarer_une_section_d_administration(copy.deepcopy(SCIM))
        assert [s["id"] for s in greffons.sections_declarees()] == ["scim"]
        autre = copy.deepcopy(SCIM)
        autre["title"] = "une autre section"
        with pytest.raises(ValueError, match="deux fois"):
            greffons.declarer_une_section_d_administration(autre)
    finally:
        greffons.reinitialiser()


async def test_une_section_d_organisation_qui_demande_la_plateforme_ne_se_montre_qu_a_elle(
    greffon: Any, client: AsyncClient, admin: str
) -> None:
    """`org_admin` a toutes les permissions dans son organisation, `platform:admin` comprise : sans
    garde, la section des plafonds d'une organisation — que seule la plateforme peut écrire — se
    montrait à l'administrateur de n'importe quel locataire, et chaque enregistrement rendait 403."""
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    greffon.declarer_une_section_d_administration(
        {
            "id": "limites",
            "title": "limits",
            "scope": "organisation",
            "permission": "platform:admin",
            "blocks": [
                {"kind": "table", "title": "t", "list": "/orgs", "columns": [{"key": "slug", "label": "s"}]}
            ],
        }
    )
    ids = [s["id"] for s in (await client.get("/api/v1/ui/admin-sections")).json()]
    assert ids == ["scim", "organisations", "limites"], (
        "une seule organisation : son administrateur a l'instance"
    )

    # Une seconde organisation : l'administrateur de `varga` n'administre plus l'instance.
    async with session_scope() as session:
        session.add(Organization(slug="autre", name="Autre"))
    ids = [s["id"] for s in (await client.get("/api/v1/ui/admin-sections")).json()]
    assert ids == ["scim"], "member:manage dans son organisation, rien de la plateforme"
