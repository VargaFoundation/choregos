"""Renommer une organisation (`PATCH /orgs/{org}`).

Le dev de Choregos tournait dans une organisation `diametral` — le nom de l'hébergeur, dans un projet
open source — et rien ne savait la renommer ; l'édition communautaire n'en tient qu'une, donc rien
ne savait non plus en créer une autre pour y passer. Son nom se change par son administrateur ; son
slug, par l'administrateur de la plateforme, parce qu'il est dans chaque URL, chaque adresse MCP et
le nom des groupes de l'IdP. Les projets suivent : ils tiennent à l'organisation par son identifiant.

Sur PostgreSQL (suite de la CI), la RLS compare les slugs : renommer dans une transaction bornée à
l'ancien slug serait refusé — c'est ce que ce fichier éprouve aussi.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .conftest import login

ORG = "/api/v1/orgs"


async def test_le_slug_change_et_les_projets_suivent(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    renomme = await client.patch(f"{ORG}/varga", json={"slug": "fondation", "name": "Fondation Varga"})
    assert renomme.status_code == 200, renomme.text
    assert renomme.json() == {"slug": "fondation", "name": "Fondation Varga", "role": "org_admin"}
    assert (await client.get(f"{ORG}/varga/projects")).status_code == 404
    projets = (await client.get(f"{ORG}/fondation/projects")).json()["items"]
    assert [p["slug"] for p in projets] == ["billing-api"]
    lu = await client.get("/api/v1/projects/fondation:billing-api")
    assert lu.status_code == 200, "l'identifiant qualifié suit le nouveau slug"
    assert {o["slug"] for o in (await client.get(ORG)).json()} == {"fondation"}
    (ligne,) = [a for a in (await client.get("/api/v1/audit")).json()["items"] if a["action"] == "org.update"]
    assert ligne["payload"]["avant"] == {"slug": "varga", "name": "Varga Foundation"}
    assert ligne["payload"]["apres"] == {"slug": "fondation", "name": "Fondation Varga"}


async def test_le_nom_seul_se_change_sans_toucher_au_slug(client: AsyncClient, admin: str) -> None:
    renomme = await client.patch(f"{ORG}/varga", json={"name": "Varga"})
    assert renomme.status_code == 200, renomme.text
    assert renomme.json()["slug"] == "varga"
    assert {o["name"] for o in (await client.get(ORG)).json()} == {"Varga"}


async def _autre_organisation(admin_aussi: bool) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope(orgs="*") as session:
        autre = Organization(slug="autre", name="Autre")
        session.add(autre)
        await session.flush()
        if admin_aussi:
            moi = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
            session.add(Membership(user_id=moi.id, org_id=autre.id, role="org_admin"))


async def test_le_slug_est_un_geste_d_instance(client: AsyncClient, admin: str) -> None:
    """Administrer UNE organisation parmi deux ne fait pas administrer l'instance : le slug se refuse,
    le nom se change."""
    await _autre_organisation(admin_aussi=False)
    refus = await client.patch(f"{ORG}/varga", json={"slug": "libre"})
    assert refus.status_code == 403, refus.text
    assert (await client.patch(f"{ORG}/varga", json={"name": "Varga"})).status_code == 200
    assert {o["slug"] for o in (await client.get(ORG)).json()} == {"varga"}


async def test_un_slug_pris_est_un_conflit(client: AsyncClient, admin: str) -> None:
    await _autre_organisation(admin_aussi=True)
    refus = await client.patch(f"{ORG}/varga", json={"slug": "autre"})
    assert refus.status_code == 409, refus.text
    assert "varga" in {o["slug"] for o in (await client.get(ORG)).json()}


async def test_un_proprietaire_de_projet_ne_renomme_pas_l_organisation(
    client: AsyncClient, admin: str, project: dict[str, Any]
) -> None:
    membre = {"email": "chef@varga.dev", "role": "project_owner"}
    assert (await client.post(f"{ORG}/varga/members", json=membre)).status_code in {200, 201}
    await login(client, "chef@varga.dev")
    refus = await client.patch(f"{ORG}/varga", json={"name": "Chez moi"})
    assert refus.status_code == 403, refus.text
    await login(client, "admin@varga.dev")
    assert {o["name"] for o in (await client.get(ORG)).json()} == {"Varga Foundation"}


async def test_un_slug_mal_forme_est_refuse(client: AsyncClient, admin: str) -> None:
    assert (await client.patch(f"{ORG}/varga", json={"slug": "Pas Bon"})).status_code == 422
