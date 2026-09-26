"""Une installation neuve doit pouvoir se servir d'elle-même.

État des lieux du 2026-09-24 : aucun moyen de créer une organisation, aucun de poser une
demande sur un tracker interne, aucun d'émettre le premier jeton. Le produit ne
démarrait qu'avec un script de seed qui écrit en base.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import login


async def test_l_amorcage_cree_l_organisation_et_ses_admins_une_seule_fois(
    app: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.amorcage import amorcer
    from choregos_api.config import Settings

    reglages = Settings(
        bootstrap_org="acme", bootstrap_org_name="ACME", bootstrap_admins="Alice@acme.test, bob@acme.test"
    )
    premier = await amorcer(reglages)
    assert premier == {"organisations": ["acme"], "administrateurs": ["alice@acme.test", "bob@acme.test"]}
    second = await amorcer(reglages)
    assert second == {"organisations": [], "administrateurs": []}, "rejouer ne touche pas à ce qui existe"
    assert await amorcer(Settings(bootstrap_org="")) == {"organisations": [], "administrateurs": []}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        await login(client, "alice@acme.test")
        orgs = (await client.get("/api/v1/orgs")).json()
        assert [(o["slug"], o["role"]) for o in orgs] == [("acme", "org_admin")]


async def test_un_admin_cree_une_organisation_un_developpeur_non(client: AsyncClient, admin: str) -> None:
    """Le contrôle des DROITS passe avant celui de l'ÉDITION, et c'est volontaire.

    Un développeur reçoit 403 sans rien apprendre de l'édition ni du nombre d'organisations ;
    un administrateur reçoit 409 et la raison. Une limite de produit ne se raconte pas à qui
    n'a pas le droit de la rencontrer.

    Ce test créait une seconde organisation ; l'édition communautaire n'en tient qu'une
    (ADR 0024), donc il la crée désormais en édition entreprise — ce qui prouve au passage que
    la garde tient à l'édition et non à un plafond codé en dur.
    """
    from choregos_api import edition

    refus = await client.post("/api/v1/orgs", json={"slug": "filiale", "name": "La filiale"})
    assert refus.status_code == 409, refus.text
    assert "communautaire" in refus.json()["detail"]

    edition.declarer(edition.ENTREPRISE)
    try:
        created = await client.post("/api/v1/orgs", json={"slug": "filiale", "name": "La filiale"})
        assert created.status_code == 201, created.text
        assert created.json()["role"] == "org_admin"
        encore = await client.post("/api/v1/orgs", json={"slug": "filiale", "name": "encore"})
        assert encore.status_code == 409, "un slug déjà pris reste un conflit"
        slugs = [o["slug"] for o in (await client.get("/api/v1/orgs")).json()]
        assert slugs == ["filiale", "varga"]

        await login(client, "dev@varga.dev")
        assert (await client.post("/api/v1/orgs", json={"slug": "pirate", "name": "x"})).status_code == 403
    finally:
        edition.reinitialiser()


async def test_une_demande_se_pose_dans_choregos_quand_le_tracker_est_interne(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.temporal import FakeTemporal, get_temporal

    pose = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert pose.status_code in {200, 201}, pose.text
    created = await client.post(
        f"/api/v1/projects/{project['id']}/work-items",
        json={"title": "Chef de projet data", "body": "6 mois, Lille", "size": "M"},
    )
    assert created.status_code == 201, created.text
    item = created.json()
    assert item["tracker_key"] == "BILLING-API-1"
    assert item["state"] == "inbox"
    assert item["temporal_wf_id"] == "wi-billing-api-BILLING-API-1"
    fake = get_temporal()
    assert isinstance(fake, FakeTemporal) and item["temporal_wf_id"] in fake.started

    second = (
        await client.post(
            f"/api/v1/projects/{project['id']}/work-items", json={"title": "Deux devs", "start": False}
        )
    ).json()
    assert second["tracker_key"] == "BILLING-API-2" and second["temporal_wf_id"] is None

    # tracker externe : la demande se crée là-bas, pas ici
    await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "github", "config": {}}
    )
    refus = await client.post(f"/api/v1/projects/{project['id']}/work-items", json={"title": "x"})
    assert refus.status_code == 409
