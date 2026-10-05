"""Les groupes d'un projet ne se changent que par l'administrateur de l'organisation.

L'ADR 0014 tient l'accès aux outils du catalogue par DEUX verrous : le déploiement dit qui a le
droit (`groups` sur l'outil), le projet dit ce dont il se sert (`config.tools`). Mais les groupes
du projet vivaient dans la même configuration que ses outils, modifiable par le propriétaire du
projet : il s'ouvrait les outils réservés à un autre métier en s'ajoutant à son groupe, et le
second verrou ne tenait que par politesse.
"""

from __future__ import annotations

from typing import Any

from httpx import AsyncClient

from .conftest import login


async def _proprietaire(email: str) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        user = User(email=email, display_name="proprio")
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org.id, role="project_owner"))


def _config(project: dict[str, Any], **changes: Any) -> dict[str, Any]:
    config = {k: v for k, v in project["config"].items() if v is not None}
    config.update(changes)
    return {"config": config}


async def test_un_proprietaire_de_projet_ne_s_ouvre_pas_le_groupe_d_un_autre_metier(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    await _proprietaire("proprio@varga.dev")
    await login(client, "proprio@varga.dev")
    reponse = await client.patch(f"/api/v1/projects/{project['id']}", json=_config(project, groups=["rh"]))
    assert reponse.status_code == 403, reponse.text


async def test_il_garde_la_main_sur_ses_outils(client: AsyncClient, project: dict[str, Any]) -> None:
    """Le verrou ne gêne que ce qu'il doit : changer `tools`, à groupes égaux, reste à l'équipe."""
    await _proprietaire("proprio@varga.dev")
    await login(client, "proprio@varga.dev")
    reponse = await client.patch(f"/api/v1/projects/{project['id']}", json=_config(project, tools=["*"]))
    assert reponse.status_code == 200, reponse.text


async def test_l_administrateur_de_l_organisation_les_change(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    reponse = await client.patch(f"/api/v1/projects/{project['id']}", json=_config(project, groups=["rh"]))
    assert reponse.status_code == 200, reponse.text
    assert reponse.json()["config"]["groups"] == ["rh"]
