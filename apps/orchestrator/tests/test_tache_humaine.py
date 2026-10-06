"""Une tâche humaine, puis l'action qui en dépend (S20-06) : la personne de l'accueil remet le badge
et saisit son UID — la tâche —, et l'UID arrive dans les paramètres d'`activate_badge`, que la
plateforme joue sur les lecteurs (le faux de S20-04) avec la clé du connecteur."""

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
ATTESTEE = "J'ai remis le badge en main propre à son porteur"
FORMULAIRE = {"type": "object", "required": ["badge_uid"], "properties": {"badge_uid": {"type": "string"}}}
BADGE: dict[str, Any] = {
    "apiVersion": "choregos/v1",
    "kind": "Workflow",
    "metadata": {
        "name": "onboarding",
        "version": 1,
        "inputs": {
            "type": "object",
            "properties": {"upn": {"type": "string"}, "badge_uid": {"type": "string"}},
        },
    },
    "initial": "badge",
    "actors": {"accueil": {"type": "human", "group": "accueil"}, "plateforme": {"type": "system"}},
    "states": {
        "badge": {"display": "Badge à remettre", "kind": "wait"},
        "remis": {"display": "Badge remis", "kind": "wait"},
        "a_revoir": {"display": "À revoir", "kind": "wait"},
        "actif": {"display": "Badge actif", "terminal": True},
    },
    "transitions": [
        {
            "id": "t-badge",
            "from": "badge",
            "to": "remis",
            "by": "accueil",
            "task": {"title": "Remettre le badge", "form": FORMULAIRE, "attest": ATTESTEE},
        },
        {
            "id": "t-activer",
            "from": "remis",
            "to": "actif",
            "by": "plateforme",
            "action": {
                "kind": "badge.activer",
                "title": "Activer le badge {{ fields.badge_uid }}",
                "params": {"uid": "{{ fields.badge_uid }}", "porteur": "{{ fields.upn }}"},
                "effects": [
                    {
                        "effect": "connector.call",
                        "with": {
                            "connector": "badges",
                            "operation": "activate_badge",
                            "arguments": {"uid": "{{ params.uid }}", "holder": "{{ params.porteur }}"},
                        },
                    }
                ],
            },
            "on_fail": {"to": "remis", "max_attempts": 1, "escalate_to": "a_revoir"},
        },
    ],
}


@pytest.fixture
def lecteurs(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    import choregos_adapters
    from choregos_adapters.fakes.rh import FakeBadges

    faux = FakeBadges(cle_attendue="cle-des-lecteurs")
    monkeypatch.setitem(choregos_adapters.FAUX_METIER, "access_control", faux)
    monkeypatch.setenv("CHOREGOS_TEST_CLE_LECTEURS", "cle-des-lecteurs")
    yield faux


async def _decor(setup: Fixture) -> None:
    """Les lecteurs de badges de l'organisation (type `demo`, clé en référence), `activate_badge`
    ouverte (`allowed`) ; le workflow ; le ticket de Léa, à la tâche."""
    from choregos_adapters import spec_of
    from choregos_api.db.models import ConnectorOperation, OrgConnector, Project, WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_contracts import Workflow

    spec = spec_of("access_control", "demo")
    assert spec is not None
    activer = next(o for o in spec.operations if o.name == "activate_badge")
    async with session_scope() as session:
        projet = await session.get(Project, setup.project_id)
        assert projet is not None
        lecteurs = OrgConnector(
            org_id=projet.org_id,
            name="badges",
            kind="access_control",
            type="demo",
            config={},
            secret_refs={"api_key": "env:CHOREGOS_TEST_CLE_LECTEURS"},
        )
        session.add(lecteurs)
        await session.flush()
        session.add(
            ConnectorOperation(
                org_id=projet.org_id,
                connector_id=lecteurs.id,
                name="activate_badge",
                access="write",
                policy="allowed",
                groups=[],
                input_schema=activer.input_schema,
            )
        )
        modele = Workflow.model_validate(BADGE)
        ligne = WorkflowDef(
            project_id=setup.project_id,
            name="onboarding",
            version=1,
            source="platform",
            yaml="",
            json_doc=modele.model_dump(mode="json", by_alias=True, exclude_none=True),
            checksum="sha256:onboarding-badge",
            is_active=True,
        )
        session.add(ligne)
        await session.flush()
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id, item.state, item.fields = ligne.id, "badge", {"upn": "lea@acme.test"}


async def _attendre(handle: Any, predicat: Any, quoi: str) -> dict[str, Any]:
    for _ in range(600):
        status = await handle.query("status")
        if predicat(status):
            return dict(status)
        await asyncio.sleep(0.05)
    raise AssertionError(f"{quoi} : jamais vrai (status : {await handle.query('status')})")


async def test_l_uid_saisi_dans_la_tache_arrive_dans_les_parametres_d_activate_badge(
    setup: Fixture, temporal_env: Any, worker_factory: Any, lecteurs: Any
) -> None:
    from choregos_api.db.models import Action, HumanRequest, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from sqlalchemy import select
    from temporalio.api.enums.v1 import EventType
    from temporalio.worker import Replayer

    await _decor(setup)
    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "WorkflowInterpreter",
            {
                "project_id": setup.project_id,
                "project_slug": setup.project_slug,
                "work_item_id": setup.work_item_id,
                "tracker_key": setup.tracker_key,
            },
            id=f"wi-{setup.project_slug}-badge",
            task_queue="test",
        )
        statut = await _attendre(handle, lambda s: s.get("pending_request"), "la tâche est demandée")
        async with session_scope() as session:
            demande = await session.get(HumanRequest, statut["pending_request"])
            assert demande is not None
            assert (demande.kind, demande.payload["form"], demande.payload["attest"]) == (
                "task",
                FORMULAIRE,
                ATTESTEE,
            )
            # Ce que fait l'API (`complete`) : les valeurs dans les champs, puis la décision au ticket.
            item = await session.get(WorkItem, setup.work_item_id)
            assert item is not None
            item.fields = {**(item.fields or {}), "badge_uid": "04A1B2C3"}
        await handle.signal(
            "human_decision",
            {
                "request_id": statut["pending_request"],
                "kind": "task",
                "approved": True,
                "values": {"badge_uid": "04A1B2C3"},
                "attestation": ATTESTEE,
                "decided_by": "accueil@acme.test",
            },
        )
        resultat = await handle.result()
        historique = await handle.fetch_history()
    assert resultat["state"] == "actif"
    assert lecteurs.badges == {"04A1B2C3": {"uid": "04A1B2C3", "holder": "lea@acme.test", "active": True}}
    signaux = [
        e.workflow_execution_signaled_event_attributes.signal_name
        for e in historique.events
        if e.event_type == EventType.EVENT_TYPE_WORKFLOW_EXECUTION_SIGNALED
    ]
    assert "action_settled" in signaux, (
        "l'action prévient l'interpréteur qui l'a proposée, sous SON identifiant"
    )
    async with session_scope() as session:
        (action,) = (await session.execute(select(Action))).scalars()
        assert action.params == {"uid": "04A1B2C3", "porteur": "lea@acme.test"}
        assert action.title == "Activer le badge 04A1B2C3" and action.status == "succeeded"
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(historique)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        cible = HISTORIQUES / "wi-tache-badge-S20-06.json"
        cible.write_text(json.dumps(json.loads(historique.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_une_date_saisie_dans_la_tache_arme_l_action_qui_en_depend(
    setup: Fixture, temporal_env: Any, worker_factory: Any, lecteurs: Any
) -> None:
    """La tâche remplit la date que l'action suivante attend (`not_before`) : ses valeurs entrent dans
    les champs que l'interpréteur tient, pas seulement en base."""
    from choregos_api.db.models import WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_contracts import Workflow

    await _decor(setup)
    document = json.loads(json.dumps(BADGE))
    document["metadata"]["inputs"]["properties"]["remis_le"] = {"type": "string", "format": "date-time"}
    document["transitions"][0]["task"]["form"]["properties"]["remis_le"] = {"type": "string"}
    document["transitions"][1]["action"]["not_before"] = "fields.remis_le + 1h"
    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        ligne = await session.get(WorkflowDef, item.workflow_def_id)
        assert ligne is not None
        ligne.json_doc = Workflow.model_validate(document).model_dump(
            mode="json", by_alias=True, exclude_none=True
        )
    debut = await temporal_env.get_current_time()
    valeurs = {"badge_uid": "04A1B2C3", "remis_le": (debut - timedelta(hours=2)).isoformat()}
    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "WorkflowInterpreter",
            {
                "project_id": setup.project_id,
                "project_slug": setup.project_slug,
                "work_item_id": setup.work_item_id,
                "tracker_key": setup.tracker_key,
            },
            id=f"wi-{setup.project_slug}-badge-date",
            task_queue="test",
        )
        statut = await _attendre(handle, lambda s: s.get("pending_request"), "la tâche est demandée")
        async with session_scope() as session:
            item = await session.get(WorkItem, setup.work_item_id)
            assert item is not None
            item.fields = {**(item.fields or {}), **valeurs}
        await handle.signal(
            "human_decision",
            {"request_id": statut["pending_request"], "kind": "task", "approved": True, "values": valeurs,
             "attestation": ATTESTEE, "decided_by": "accueil@acme.test"},
        )  # fmt: skip
        # Sans avancer l'horloge : la date saisie est passée d'une heure, l'action part tout de suite.
        await _attendre(handle, lambda s: s["state"] == "actif", "le badge est actif")
    assert lecteurs.badges["04A1B2C3"]["active"] is True
