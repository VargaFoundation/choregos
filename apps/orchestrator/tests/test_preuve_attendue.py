"""Une action attend la PREUVE qu'un de ses effets a servi (S20-08) : un rapport à venir, un événement
extérieur. L'effet le demande (`attendre_une_preuve`), le workflow attend le signal `preuve` jusqu'à
l'échéance : favorable, l'action réussit ; contraire ou absente, elle échoue — et ce qui était fait
se compense, à rebours, comme pour un refus.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest

from .conftest import Fixture

HISTORIQUES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


@pytest.fixture
def faits() -> Iterator[list[tuple[str, dict[str, Any]]]]:
    from choregos_api import effets

    vus: list[tuple[str, dict[str, Any]]] = []

    async def creer(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        vus.append(("creer", params))
        return {"id": "correctif-1"}

    async def attendre(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        vus.append(("attendre", params))
        return {"attendre_une_preuve": {"jusqu_a": params["jusqu_a"]}}

    async def defaire(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        vus.append(("defaire", params))
        return {}

    for nom, fonction in (("test.creer", creer), ("test.attendre", attendre), ("test.defaire", defaire)):
        effets.declarer_un_effet(nom, fonction)
    yield vus
    effets.reinitialiser()


async def _action(setup: Fixture, jusqu_a: str) -> str:
    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        projet = await session.get(Project, setup.project_id)
        assert projet is not None
        action = Action(
            org_id=projet.org_id,
            project_id=projet.id,
            origin="ontology",
            kind="ontology.verify_finding_fixed",
            title="Le correctif, et la preuve qu'il a servi",
            params={"jusqu_a": jusqu_a},
            effects=[
                {"effect": "test.creer", "with": {}, "compensate": {"effect": "test.defaire", "with": {}}},
                {"effect": "test.attendre", "with": {"jusqu_a": "{{ params.jusqu_a }}"}},
            ],
            proposed_by={"kind": "system", "id": "system"},
            approval={},
            decisions=[],
            status="approved",
        )
        session.add(action)
        await session.flush()
        return str(action.id)


async def _ligne(action_id: str) -> Any:
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return await session.get(Action, action_id)


async def _demarrer(temporal_env: Any, action_id: str) -> Any:
    return await temporal_env.client.start_workflow(
        "ActionWorkflow", {"action_id": action_id}, id=f"action-{action_id}", task_queue="test"
    )


async def _en_attente(action_id: str) -> Any:
    for _ in range(400):
        ligne = await _ligne(action_id)
        if ligne.status == "awaiting_evidence":
            return ligne
        await asyncio.sleep(0.05)
    raise AssertionError(f"l'action n'attend jamais sa preuve ({(await _ligne(action_id)).status})")


async def test_une_action_attend_sa_preuve_et_reussit_quand_elle_vient(
    setup: Fixture, temporal_env: Any, worker_factory: Any, faits: list[Any]
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    jusqu_a = (await temporal_env.get_current_time() + timedelta(hours=1)).isoformat()
    action_id = await _action(setup, jusqu_a)
    async with worker_factory():
        handle = await _demarrer(temporal_env, action_id)
        ligne = await _en_attente(action_id)
        assert ligne.result["attente"] == {"position": 1, "jusqu_a": jusqu_a}
        await handle.signal(
            "preuve", {"position": 1, "ok": True, "detail": "la clé est absente du rapport suivant"}
        )
        resultat = await handle.result()
        historique = await handle.fetch_history()
    assert resultat["status"] == "succeeded"
    ligne = await _ligne(action_id)
    assert ligne.status == "succeeded" and "attente" not in ligne.result
    assert ligne.result["preuves"][0]["detail"] == "la clé est absente du rapport suivant"
    assert [nom for nom, _ in faits] == ["creer", "attendre"], "rien n'est défait"
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(historique)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        cible = HISTORIQUES / "action-preuve-S20-08.json"
        cible.write_text(json.dumps(json.loads(historique.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_une_preuve_contraire_fait_echouer_l_action_et_compense(
    setup: Fixture, temporal_env: Any, worker_factory: Any, faits: list[Any]
) -> None:
    jusqu_a = (await temporal_env.get_current_time() + timedelta(hours=1)).isoformat()
    action_id = await _action(setup, jusqu_a)
    async with worker_factory():
        handle = await _demarrer(temporal_env, action_id)
        await _en_attente(action_id)
        await handle.signal("preuve", {"position": 1, "ok": False, "detail": "la clé est toujours là"})
        resultat = await handle.result()
    assert (
        resultat["status"] == "failed" and "the evidence says no: la clé est toujours là" in resultat["error"]
    )
    assert [nom for nom, _ in faits] == ["creer", "attendre", "defaire"], "ce qui était fait se défait"
    assert (await _ligne(action_id)).status == "failed"


async def test_sans_preuve_avant_l_echeance_l_action_echoue(
    setup: Fixture, temporal_env: Any, worker_factory: Any, faits: list[Any]
) -> None:
    jusqu_a = (await temporal_env.get_current_time() + timedelta(minutes=15)).isoformat()
    action_id = await _action(setup, jusqu_a)
    async with worker_factory():
        handle = await _demarrer(temporal_env, action_id)
        await _en_attente(action_id)
        resultat = await handle.result()  # le temps saute jusqu'à l'échéance
    assert resultat["status"] == "failed" and "timed out" in resultat["error"]
    assert [nom for nom, _ in faits][-1] == "defaire"
