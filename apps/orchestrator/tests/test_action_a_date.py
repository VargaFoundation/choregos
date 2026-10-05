"""Une transition système propose une action gouvernée, à date (S20-05) — en temps accéléré.

Le ticket attend J-10 de sa date d'arrivée ; avant, rien n'est proposé. Déplacer la date réarme le
minuteur ; l'avancer sous l'heure présente fait partir l'action aussitôt. L'action se joue dans son
`ActionWorkflow`, jamais dans l'interpréteur, qui l'apprend par `action_settled`. Rejetée ou
échouée, elle suit `on_fail` ; impossible à proposer, elle le dit.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import pytest

from .conftest import Fixture

HISTORIQUES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


def _arrivee(politique: str = "allowed", parametres: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "apiVersion": "choregos/v1",
        "kind": "Workflow",
        "metadata": {
            "name": "onboarding",
            "version": 1,
            "inputs": {
                "type": "object",
                "properties": {
                    "upn": {"type": "string"},
                    "date_arrivee": {"type": "string", "format": "date-time"},
                },
            },
        },
        "initial": "prepare",
        "actors": {"plateforme": {"type": "system"}},
        "states": {
            "prepare": {"display": "Préparée", "kind": "wait"},
            "a_revoir": {"display": "À revoir", "kind": "wait"},
            "fait": {"display": "Fait", "terminal": True},
        },
        "transitions": [
            {
                "id": "t-comptes",
                "from": "prepare",
                "to": "fait",
                "by": "plateforme",
                "action": {
                    "kind": "arrivee.comptes",
                    "title": "Les comptes de {{ fields.upn }}",
                    "params": parametres or {"upn": "{{ fields.upn }}"},
                    "effects": [{"effect": f"test.creer.{politique}", "with": {"upn": "{{ params.upn }}"}}],
                    "not_before": "fields.date_arrivee - 10d",
                },
                "on_fail": {"to": "prepare", "max_attempts": 1, "escalate_to": "a_revoir"},
            }
        ],
    }


@pytest.fixture
def faits() -> Iterator[list[dict[str, Any]]]:
    """Ce que les effets ont VRAIMENT fait : la vérité du monde extérieur."""
    from choregos_api import effets

    vus: list[dict[str, Any]] = []

    async def creer(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        vus.append(params)
        return {"id": f"compte-{params['upn']}"}

    effets.declarer_un_effet("test.creer.allowed", creer, politique="allowed")
    effets.declarer_un_effet("test.creer.approval", creer, politique="approval")
    yield vus
    effets.reinitialiser()


async def _ticket(setup: Fixture, document: dict[str, Any], champs: dict[str, Any]) -> None:
    from choregos_api.db.models import WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_contracts import Workflow

    modele = Workflow.model_validate(document)
    async with session_scope() as session:
        ligne = WorkflowDef(
            project_id=setup.project_id,
            name="onboarding",
            version=1,
            source="platform",
            yaml="",
            json_doc=modele.model_dump(mode="json", by_alias=True, exclude_none=True),
            checksum="sha256:onboarding",
            is_active=True,
        )
        session.add(ligne)
        await session.flush()
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id, item.state, item.fields = ligne.id, "prepare", champs


async def _demarrer(env: Any, setup: Fixture) -> Any:
    return await env.client.start_workflow(
        "WorkflowInterpreter",
        {
            "project_id": setup.project_id,
            "project_slug": setup.project_slug,
            "work_item_id": setup.work_item_id,
            "tracker_key": setup.tracker_key,
        },
        id=f"wi-{setup.project_slug}-varga_billing-api-123",
        task_queue="test",
    )


async def _attendre(handle: Any, predicat: Any, quoi: str) -> dict[str, Any]:
    for _ in range(600):
        status = await handle.query("status")
        if predicat(status):
            return dict(status)
        await asyncio.sleep(0.05)
    raise AssertionError(f"{quoi} : jamais vrai (status : {await handle.query('status')})")


async def _actions(setup: Fixture) -> list[Any]:
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        lignes = await session.execute(select(Action).where(Action.work_item_id == setup.work_item_id))
        return list(lignes.scalars())


def _iso(instant: datetime) -> str:
    return instant.isoformat()


async def test_l_action_attend_j_moins_10_puis_part_et_le_ticket_l_apprend_par_un_signal(
    setup: Fixture, temporal_env: Any, worker_factory: Any, faits: list[dict[str, Any]]
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.api.enums.v1 import EventType
    from temporalio.worker import Replayer

    debut = await temporal_env.get_current_time()
    arrivee = debut + timedelta(days=20)
    await _ticket(setup, _arrivee(), {"upn": "lea@acme.test", "date_arrivee": _iso(arrivee)})
    async with worker_factory():
        handle = await _demarrer(temporal_env, setup)
        statut = await _attendre(handle, lambda s: s.get("waiting_until"), "le ticket attend sa date")
        assert datetime.fromisoformat(statut["waiting_until"]) == arrivee - timedelta(days=10)
        await temporal_env.sleep(timedelta(days=9, hours=23))
        assert await _actions(setup) == [], "rien n'est proposé avant J-10"
        assert faits == []
        await temporal_env.sleep(timedelta(hours=2))
        resultat = await handle.result()
        historique = await handle.fetch_history()
    assert resultat["state"] == "fait"
    assert faits == [{"upn": "lea@acme.test"}], "l'effet a été joué une fois"
    (action,) = await _actions(setup)
    assert (action.origin, action.status, action.title) == (
        "transition",
        "succeeded",
        "Les comptes de lea@acme.test",
    )
    assert action.decisions[0]["by"] == "policy", "chaque opération est permise : la politique a décidé"
    assert action.proposed_by["transition"] == "t-comptes"
    signaux = [
        e.workflow_execution_signaled_event_attributes.signal_name
        for e in historique.events
        if e.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED
    ]
    assert "action_settled" in signaux, "l'ActionWorkflow prévient le ticket : il n'attend pas sa relecture"
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(historique)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        cible = HISTORIQUES / "wi-action-a-date-S20-05.json"
        cible.write_text(json.dumps(json.loads(historique.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_deplacer_la_date_rearme_le_minuteur_et_l_avancer_dans_le_passe_fait_partir(
    setup: Fixture, temporal_env: Any, worker_factory: Any, faits: list[dict[str, Any]]
) -> None:
    debut = await temporal_env.get_current_time()
    champs = {"upn": "lea@acme.test", "date_arrivee": _iso(debut + timedelta(days=20))}
    await _ticket(setup, _arrivee(), champs)
    async with worker_factory():
        handle = await _demarrer(temporal_env, setup)
        await _attendre(handle, lambda s: s.get("waiting_until"), "le ticket attend sa date")
        await temporal_env.sleep(timedelta(days=5))
        plus_tard = debut + timedelta(days=30)
        await handle.signal("fields_changed", {"fields": {**champs, "date_arrivee": _iso(plus_tard)}})
        await _attendre(
            handle,
            lambda s: s.get("waiting_until") == _iso(plus_tard - timedelta(days=10)),
            "le minuteur est réarmé sur la nouvelle date",
        )
        await temporal_env.sleep(timedelta(days=7))  # J+12 : l'ancienne échéance (J+10) est passée
        assert await _actions(setup) == [] and faits == [], "l'ancienne date ne déclenche plus rien"
        plus_tot = debut + timedelta(days=15)  # J-10 tombe à J+5 : déjà passé
        await handle.signal("fields_changed", {"fields": {**champs, "date_arrivee": _iso(plus_tot)}})
        # Sans avancer l'horloge : la date passée fait partir l'action tout de suite, elle n'attend
        # pas l'ancien minuteur.
        await _attendre(handle, lambda s: s["state"] == "fait", "l'action part aussitôt")
        resultat = await handle.result()
    assert resultat["state"] == "fait"
    assert faits == [{"upn": "lea@acme.test"}]


async def test_un_ticket_en_pause_ne_part_pas_a_sa_date_il_part_a_la_reprise(
    setup: Fixture, temporal_env: Any, worker_factory: Any, faits: list[dict[str, Any]]
) -> None:
    debut = await temporal_env.get_current_time()
    champs = {"upn": "lea@acme.test", "date_arrivee": _iso(debut + timedelta(days=12))}
    await _ticket(setup, _arrivee(), champs)
    async with worker_factory():
        handle = await _demarrer(temporal_env, setup)
        await _attendre(handle, lambda s: s.get("waiting_until"), "le ticket attend sa date")
        await handle.signal("control", {"action": "pause"})
        await _attendre(handle, lambda s: s["paused"], "le ticket est en pause")
        await temporal_env.sleep(timedelta(days=3))  # J-10 est passé
        assert await _actions(setup) == [] and faits == [], "en pause, rien ne part"
        await handle.signal("control", {"action": "resume"})
        await _attendre(handle, lambda s: s["state"] == "fait", "la reprise fait partir l'action")
    assert faits == [{"upn": "lea@acme.test"}]


async def test_un_signal_perdu_la_relecture_prend_le_relais(
    setup: Fixture,
    temporal_env: Any,
    worker_factory: Any,
    faits: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`action_settled` n'arrive jamais (un ticket injoignable un instant) : la relecture en base,
    toutes les six heures, règle l'attente quand même."""
    from choregos_orchestrator.activities import actions

    async def perdu(*_: Any) -> None:
        return None

    monkeypatch.setattr(actions, "_prevenir", perdu)
    debut = await temporal_env.get_current_time()
    await _ticket(setup, _arrivee(), {"upn": "lea@acme.test", "date_arrivee": _iso(debut)})
    async with worker_factory():
        handle = await _demarrer(temporal_env, setup)
        resultat = await handle.result()
    assert resultat["state"] == "fait" and faits == [{"upn": "lea@acme.test"}]


async def test_une_action_rejetee_n_est_pas_jouee_et_le_ticket_suit_on_fail(
    setup: Fixture, temporal_env: Any, worker_factory: Any, faits: list[dict[str, Any]]
) -> None:
    """Une opération sous validation : l'action attend une personne. Rejetée — ce que fait
    `decider` dans l'API : la ligne, puis `action_settled` —, rien n'est joué, et le ticket va
    où `on_fail` l'envoie."""
    from choregos_api.db.models import Action
    from choregos_api.db.session import session_scope

    debut = await temporal_env.get_current_time()
    champs = {"upn": "lea@acme.test", "date_arrivee": _iso(debut + timedelta(days=5))}
    await _ticket(setup, _arrivee(politique="approval"), champs)
    async with worker_factory():
        handle = await _demarrer(temporal_env, setup)
        statut = await _attendre(handle, lambda s: s.get("action"), "l'action est proposée")
        (action,) = await _actions(setup)
        assert (action.id, action.status, action.decisions) == (statut["action"], "pending_approval", [])
        async with session_scope() as session:
            ligne = await session.get(Action, action.id)
            assert ligne is not None
            ligne.status = "rejected"
        await handle.signal("action_settled", {"action_id": action.id, "status": "rejected"})
        await _attendre(handle, lambda s: s["state"] == "a_revoir", "le ticket est à revoir")
        await handle.signal("control", {"action": "stop"})
    assert faits == [], "rejetée : aucun effet"


async def test_une_action_impossible_a_proposer_le_dit_et_le_ticket_suit_on_fail(
    setup: Fixture, temporal_env: Any, worker_factory: Any, faits: list[dict[str, Any]]
) -> None:
    debut = await temporal_env.get_current_time()
    champs = {"upn": "lea@acme.test", "date_arrivee": _iso(debut)}
    await _ticket(setup, _arrivee(parametres={"upn": "{{ fields.matricule }}"}), champs)
    async with worker_factory():
        handle = await _demarrer(temporal_env, setup)
        await _attendre(handle, lambda s: s["state"] == "a_revoir", "le ticket est à revoir")
        await handle.signal("control", {"action": "stop"})
    assert await _actions(setup) == [] and faits == []
