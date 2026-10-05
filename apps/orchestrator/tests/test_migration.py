"""`migrate` : le ticket change de définition sans mourir (ADR 0031, S16-05).

L'API transmettait l'identifiant de la cible, et l'interpréteur validait le message de contrôle
LUI-MÊME comme une définition : `Workflow.model_validate` levait dans le code du workflow, et le
ticket mourait. La définition arrive désormais dans le signal, lue par l'API ; l'interpréteur
remappe l'état, consigne la migration, et l'épingle du ticket suit — un `continue_as_new` rechargera
la cible, pas l'ancienne. Une migration devenue impossible laisse le ticket en vie, sur sa définition.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from typing import Any

import pytest

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

HISTORIQUES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"

#: Un ticket garé : un état d'attente sans transition sortante, que seul le board ferait bouger.
GARAGE: dict[str, Any] = {
    "apiVersion": "choregos/v1",
    "kind": "Workflow",
    "metadata": {"name": "garage", "version": 1},
    "initial": "gare",
    "actors": {"rh": {"type": "human", "group": "rh"}},
    "states": {"gare": {"display": "Garé", "kind": "wait"}, "fait": {"display": "Fait", "terminal": True}},
    "transitions": [],
}
ARRIVEE: dict[str, Any] = {
    "apiVersion": "choregos/v1",
    "kind": "Workflow",
    "metadata": {"name": "onboarding", "version": 1},
    "initial": "demande",
    "actors": {"plateforme": {"type": "system"}},
    "states": {
        "demande": {"display": "Demande", "kind": "wait"},
        "fait": {"display": "Fait", "terminal": True},
    },
    "transitions": [{"id": "t-faire", "from": "demande", "to": "fait", "by": "plateforme"}],
}


async def _definir(setup: Fixture, document: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Une définition en base, et ce que l'API en enverrait dans le signal."""
    from choregos_api.db.models import WorkflowDef
    from choregos_api.db.session import session_scope
    from choregos_contracts import Workflow

    modele = Workflow.model_validate(document)
    signal = modele.model_dump(mode="json", by_alias=True, exclude_none=True)
    async with session_scope() as session:
        ligne = WorkflowDef(
            project_id=setup.project_id,
            name=modele.metadata.name,
            version=modele.metadata.version,
            source="platform",
            yaml="",
            json_doc=signal,
            checksum=f"sha256:{modele.metadata.name}",
            is_active=True,
        )
        session.add(ligne)
        await session.flush()
        return ligne.id, signal


async def _garer(setup: Fixture) -> str:
    """Le ticket naît dans `garage`, à l'état `gare`."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    garage, _ = await _definir(setup, GARAGE)
    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id, item.state = garage, "gare"
    return garage


async def _demarrer(env: Any, setup: Fixture) -> Any:
    return await env.client.start_workflow(
        "WorkflowInterpreter",
        {
            "project_id": setup.project_id,
            "project_slug": setup.project_slug,
            "work_item_id": setup.work_item_id,
            "tracker_key": setup.tracker_key,
        },
        id=f"wi-{setup.project_slug}-migration",
        task_queue="test",
    )


async def _attendre(handle: Any, predicat: Any, quoi: str) -> dict[str, Any]:
    for _ in range(600):
        status = await handle.query("status")
        if predicat(status):
            return dict(status)
        await asyncio.sleep(0.1)
    raise AssertionError(f"{quoi} : jamais vrai (status : {await handle.query('status')})")


async def _evenements(setup: Fixture) -> list[Any]:
    from choregos_api.db.models import Event
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        lignes = await session.execute(
            select(Event).where(Event.work_item_id == setup.work_item_id, Event.type.like("%migrat%"))
        )
        return list(lignes.scalars())


async def test_un_ticket_gare_migre_et_finit_sur_sa_nouvelle_definition(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await _garer(setup)
    arrivee, signal = await _definir(setup, ARRIVEE)
    async with worker_factory():
        handle = await _demarrer(temporal_env, setup)
        await _attendre(handle, lambda s: s["state"] == "gare", "le ticket est garé")
        await handle.signal(
            "control",
            {
                "action": "migrate",
                "workflow_def_id": arrivee,
                "workflow": signal,
                "state_mapping": {"gare": "demande"},
                "by": "admin@varga.dev",
            },
        )
        # Sans réveil, un ticket garé n'aurait jamais migré : le test le dirait, au lieu d'attendre.
        resultat = await asyncio.wait_for(handle.result(), timeout=120)
        historique = await handle.fetch_history()

    assert resultat["state"] == "fait", "la transition système de la NOUVELLE définition a joué"
    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        assert item.workflow_def_id == arrivee, "l'épingle suit : un continue_as_new rechargerait la cible"
        assert item.failure is None
    (migration,) = await _evenements(setup)
    assert migration.type == "choregos.workitem.migrated"
    assert (migration.payload["from_state"], migration.payload["to_state"]) == ("gare", "demande")

    # Le nouvel historique rejoue contre le code courant — et s'archive pour les suivants.
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(historique)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        cible = HISTORIQUES / "wi-migration-S16-05.json"
        cible.write_text(json.dumps(json.loads(historique.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_une_migration_impossible_laisse_le_ticket_en_vie(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    garage = await _garer(setup)
    arrivee, signal = await _definir(setup, ARRIVEE)
    async with worker_factory():
        handle = await _demarrer(temporal_env, setup)
        await _attendre(handle, lambda s: s["state"] == "gare", "le ticket est garé")
        # Aucun mapping : `gare` n'existe pas dans `onboarding`.
        await handle.signal("control", {"action": "migrate", "workflow_def_id": arrivee, "workflow": signal})
        for _ in range(600):
            if await _evenements(setup):
                break
            await asyncio.sleep(0.1)
        status = await handle.query("status")
        assert (status["state"], status["stopped"]) == ("gare", False), "le ticket vit, sur sa définition"
        await handle.signal("control", {"action": "stop"})
        resultat = await handle.result()

    assert resultat["state"] == "gare"
    (refus,) = await _evenements(setup)
    assert refus.type == "choregos.workitem.migration_refused"
    assert "gare" in refus.payload["reason"]
    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        assert item.workflow_def_id == garage and item.failure is None


async def test_consigner_deux_fois_la_meme_migration_ne_la_double_pas(setup: Fixture) -> None:
    """Une activité rejouée (ADR 0008) : la clé de la migration est dans l'événement."""
    from choregos_orchestrator.activities.interpretation import record_migration

    arrivee, _ = await _definir(setup, ARRIVEE)
    consigne = {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "cle": "wi-x/run-1/1",
        "from_state": "inbox",
        "to_state": "demande",
        "workflow_def_id": arrivee,
    }
    assert (await record_migration(consigne))["recorded"] is True
    assert (await record_migration(consigne))["recorded"] is False
    assert len(await _evenements(setup)) == 1
