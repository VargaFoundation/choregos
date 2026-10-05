"""Un ticket garde la version du workflow où il est né (ADR 0031).

L'interpréteur rechargeait « le workflow actif du projet » à chaque démarrage, y compris à chaque
`continue_as_new` : publier une version nouvelle pendant qu'un ticket attendait le faisait changer de
règles en cours de route — et, avec deux workflows dans un projet, la version active la plus haute,
quel que soit son nom, l'emportait. `load_context` lit désormais l'épingle du ticket, et la pose
quand elle manque.
"""

from __future__ import annotations

from typing import Any

from .conftest import Fixture


async def _publier_une_v2(setup: Fixture, v1: str) -> str:
    from choregos_api.db.models import WorkflowDef
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        ancienne = await session.get(WorkflowDef, v1)
        assert ancienne is not None
        ancienne.is_active = False
        await session.flush()
        document: dict[str, Any] = dict(ancienne.json_doc)
        document["states"] = {
            **document["states"],
            "inbox": {**document["states"]["inbox"], "display": "Boîte v2"},
        }
        nouvelle = WorkflowDef(
            project_id=setup.project_id,
            name=ancienne.name,
            version=ancienne.version + 1,
            source="platform",
            yaml=ancienne.yaml,
            json_doc=document,
            checksum="sha256:v2",
            is_active=True,
        )
        session.add(nouvelle)
        await session.flush()
        return nouvelle.id


async def test_un_ticket_garde_la_version_ou_il_est_ne(setup: Fixture) -> None:
    from choregos_orchestrator.activities.interpretation import load_context

    contexte = {"project_id": setup.project_id, "work_item_id": setup.work_item_id}
    premier = await load_context(contexte)
    v1 = premier["workflow_def_id"]
    assert v1, "un ticket d'avant l'épingle la reçoit à son premier démarrage"

    v2 = await _publier_une_v2(setup, v1)
    # Un `continue_as_new`, un redémarrage de worker : l'interpréteur recharge son contexte.
    second = await load_context(contexte)
    assert second["workflow_def_id"] == v1 != v2
    assert second["workflow"]["states"]["inbox"]["display"] != "Boîte v2"


async def test_un_ticket_ne_lit_jamais_le_workflow_d_un_autre_projet(setup: Fixture) -> None:
    """Une épingle qui désignerait la définition d'un autre projet est ignorée : le défaut sert."""
    from choregos_api.db.models import Organization, Project, WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services import ensure_defaults
    from choregos_orchestrator.activities.base import workflow_du_ticket
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization))).scalars().first()
        assert org is not None
        autre = Project(org_id=org.id, slug="autre", name="Autre", status="active", config={"slug": "autre"})
        session.add(autre)
        await session.flush()
        etranger, _ = await ensure_defaults(session, autre)
        await session.flush()
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id = etranger.id
        await session.flush()
        lu = await workflow_du_ticket(session, item)
        propre = (
            await session.execute(
                select(WorkflowDef).where(
                    WorkflowDef.project_id == setup.project_id, WorkflowDef.is_active.is_(True)
                )
            )
        ).scalar_one()
        assert lu.metadata.name == propre.name
