"""Quelle édition tourne, et que refuse-t-elle ?

L'édition communautaire est **mono-organisation** ([ADR 0024](../../../docs/adr/0024-deux-editions.md)).
Ce n'est pas un plafond arbitraire : le multi-locataire n'est pas fini ici — treize tables
restent hors RLS — et une installation à une seule organisation n'est exposée à aucune de ces
fuites, parce qu'il n'y a rien à franchir.

Trois propriétés, et la deuxième est la vraie : le refus ne doit pas dépendre d'un plafond
codé en dur mais de l'édition, pour que l'édition entreprise le lève sans toucher au cœur.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from .conftest import login


@pytest.fixture(autouse=True)
def edition_par_defaut() -> Any:
    """Chaque test repart de l'édition la plus restrictive."""
    from choregos_api import edition

    edition.reinitialiser()
    yield
    edition.reinitialiser()


async def test_l_edition_se_demande(client: AsyncClient) -> None:
    """Sans route, la seule façon de savoir serait de tenter une action et de lire le refus."""
    reponse = await client.get("/edition")
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert corps["edition"] == "community"
    assert corps["features"] == []
    assert corps["version"] and corps["version"] != "1.0.0", "la version ne doit plus être en dur"


async def test_l_edition_se_demande_sous_le_prefixe_du_contrat(client: AsyncClient) -> None:
    """Le contrat la place sous `/api/v1` : c'est là que la console la demande.

    Elle n'était servie qu'à la racine, que l'Ingress envoie à la console — la console ne pouvait
    donc pas dire sur quelle édition elle tournait, et ne montrait rien de l'édition entreprise.
    """
    sous_le_prefixe = await client.get("/api/v1/edition")
    assert sous_le_prefixe.status_code == 200, sous_le_prefixe.text
    assert sous_le_prefixe.json() == (await client.get("/edition")).json()


async def test_le_communautaire_refuse_une_seconde_organisation(
    client: AsyncClient, org: str, admin: str
) -> None:
    reponse = await client.post("/api/v1/orgs", json={"slug": "seconde", "name": "Seconde"})
    assert reponse.status_code == 409, reponse.text
    detail = reponse.json()["detail"]
    assert "communautaire" in detail and "0024" in detail, "le refus doit nommer la décision"


async def test_l_entreprise_la_laisse_passer(client: AsyncClient, org: str, admin: str) -> None:
    """Le refus tient à l'ÉDITION, pas à un plafond codé en dur : sinon l'édition entreprise
    devrait forker le cœur pour le lever, ce que l'ADR 0024 refuse explicitement."""
    from choregos_api import edition

    edition.declarer(edition.ENTREPRISE, fonctions=frozenset({"multi-org"}))
    reponse = await client.post("/api/v1/orgs", json={"slug": "seconde", "name": "Seconde"})
    assert reponse.status_code == 201, reponse.text

    annonce = (await client.get("/edition")).json()
    assert annonce["edition"] == "enterprise"
    assert annonce["features"] == ["multi-org"]


async def test_sans_organisation_ce_n_est_pas_l_edition_qui_refuse(client: AsyncClient) -> None:
    """La toute première organisation vient de l'amorçage (`CHOREGOS_BOOTSTRAP_ORG`), pas de
    cette route : créer une organisation demande d'être déjà administrateur d'une organisation.

    Ce que ce test tient, c'est que la garde d'édition ne se met pas en travers de ce
    chemin-là : sur une installation vide, le refus reste un **403 de droits**, pas un 409
    d'édition. Sinon le communautaire ne serait pas mono-organisation, il serait
    zéro-organisation.
    """
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope
    from sqlalchemy import delete

    await login(client, "admin@varga.dev")
    async with session_scope(orgs="*") as session:
        await session.execute(delete(Organization))

    reponse = await client.post("/api/v1/orgs", json={"slug": "premiere", "name": "Première"})
    assert reponse.status_code == 403, reponse.text
    detail = reponse.json()["detail"]
    assert "plateforme" in detail, f"le refus doit parler de droits, pas d'édition : {detail}"
    assert "communautaire" not in detail, "ce n'est pas l'édition qui refuse ici"


async def test_administrer_la_plateforme_est_un_droit_sur_l_instance(
    client: AsyncClient, org: str, admin: str
) -> None:
    """Le défaut consigné dans BLOCKERS le 2026-09-26, et corrigé ici.

    `Principal.is_platform_admin()` rend vrai dès qu'on est `org_admin` de N'IMPORTE QUELLE
    organisation. Sur une installation à une seule organisation c'est exact — l'administrateur de
    cette organisation EST celui de l'instance. Dès qu'il y en a deux, ça ne l'est plus, et le
    prédicat accordait les routes `/platform/*` (backends, exécuteurs, profils de modèles, clés de
    passerelle) à l'administrateur de n'importe quel locataire.

    On exige désormais d'administrer TOUTES les organisations. En multi-org, ce n'est vrai de
    personne tant que l'édition entreprise n'a pas défini un vrai rôle de plateforme : un refus,
    et c'est le bon défaut — mieux vaut une route inaccessible qu'une route ouverte au mauvais
    locataire.
    """
    from choregos_api.db.models import Organization
    from choregos_api.db.session import session_scope

    # `GET /platform/gateway/keys` — budgets, dépense, expiration, révocation : la route la plus
    # sensible de `/platform/*`, et l'une des trois qui étaient gardées.
    # (Au passage : `GET /platform/backends` et `GET /platform/executors` n'ont AUCUNE garde ;
    # seuls les `PUT` en avaient. Ce n'est pas un secret, mais c'est incohérent — noté.)
    assert (await client.get("/api/v1/platform/gateway/keys")).status_code == 200

    async with session_scope(orgs="*") as session:
        session.add(Organization(slug="autre-locataire", name="Autre"))

    refus = await client.get("/api/v1/platform/gateway/keys")
    assert refus.status_code == 403, refus.text
    detail = refus.json()["detail"]
    assert "instance" in detail, f"le refus doit dire pourquoi : {detail}"
    assert "autre-locataire" in detail, "et nommer ce qui manque, sinon personne ne comprend"
