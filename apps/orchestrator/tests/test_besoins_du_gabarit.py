"""Ce dont le gabarit de développement a besoin (S21-22) : la raison d'un refus parvient à l'agent
suivant, le dépôt reçoit les étiquettes que le gabarit route, la PR porte les notes de version."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from .conftest import Fixture, stage_result

pytestmark = pytest.mark.asyncio


async def _documents(setup: Fixture) -> dict[str, Any]:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from sqlalchemy import func, select

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        runs = (
            await session.execute(select(func.count()).select_from(Run).where(Run.work_item_id == item.id))
        ).scalar_one()
        return {**dict(item.documents or {}), "__runs": int(runs)}


async def _jusqu_a(setup: Fixture, ticket: Any, predicat: Any) -> dict[str, Any]:
    for _ in range(600):
        documents, etat = await _documents(setup), await ticket.query("status")
        if predicat(documents, etat["state"]):
            return documents
        await asyncio.sleep(0.1)
    raise AssertionError(f"jamais atteint : {await _documents(setup)} / {await ticket.query('status')}")


async def test_la_raison_d_un_refus_parvient_au_ticket_et_une_approbation_l_efface(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Une transition qui déclare `inputs: [human_feedback]` la reçoit ; elle se perdait, et l'agent
    refaisait le même travail sans savoir pourquoi on le lui renvoyait."""
    for resume in ("spec v1", "spec v2"):
        setup.adapters.executor.queue_result(stage_result(resume))
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await temporal_env.client.start_workflow(
                "WorkflowInterpreter",
                {
                    "project_id": setup.project_id,
                    "project_slug": setup.project_slug,
                    "work_item_id": setup.work_item_id,
                    "tracker_key": setup.tracker_key,
                },
                id="wi-billing-api-refus",
                task_queue="test",
            )
            await _jusqu_a(setup, ticket, lambda d, etat: etat == "awaiting_spec_approval")
            refus = {
                "approved": False,
                "decided_by": "augustin",
                "kind": "approval",
                "reason": "scope too broad",
            }
            await ticket.signal("human_decision", refus)
            documents = await _jusqu_a(
                setup, ticket, lambda d, etat: d["__runs"] == 2 and etat == "awaiting_spec_approval"
            )
            assert documents["human_feedback"] == "scope too broad"

            await ticket.signal(
                "human_decision", {"approved": True, "decided_by": "augustin", "kind": "approval"}
            )
            await _jusqu_a(setup, ticket, lambda d, etat: "human_feedback" not in d)
            await ticket.cancel()


async def test_le_pas_des_etiquettes_ajoute_celles_du_gabarit_sans_toucher_a_celles_de_la_plateforme() -> (
    None
):
    from choregos_orchestrator.activities.provisioning import LABELS, _github_ensure_labels, etiquettes_du_pas

    assert etiquettes_du_pas({}) == LABELS
    en_liste = etiquettes_du_pas({"labels": ["bug", "adr"]})
    assert en_liste["bug"] == en_liste["adr"] == "ededed"
    en_couleurs = etiquettes_du_pas({"labels": {"complex": "#5319e7", "hotfix": "000000"}})
    assert en_couleurs["complex"] == "5319e7"
    assert en_couleurs["hotfix"] == LABELS["hotfix"], "une couleur de la plateforme n'est pas remplacée"

    recues: list[dict[str, str]] = []

    async def ensure_labels(etiquettes: dict[str, str]) -> None:
        recues.append(etiquettes)

    bundle = SimpleNamespace(adapters=SimpleNamespace(tracker=SimpleNamespace(ensure_labels=ensure_labels)))
    message = await _github_ensure_labels({"labels": ["study"]}, bundle, None)
    assert recues == [{**LABELS, "study": "ededed"}]
    assert message == f"{len(LABELS) + 1} labels ensured"


def test_la_pr_porte_les_notes_de_version_en_anglais() -> None:
    from choregos_orchestrator.activities.scm import _pr_body

    item = SimpleNamespace(tracker_key="varga/billing-api#7", body_snapshot="The total ignores credits.")
    corps = _pr_body(item, {"release_notes_markdown": "- Credits are now deducted from the total."})
    assert "## Release notes\n- Credits are now deducted from the total." in corps
    assert corps.startswith("Work item: varga/billing-api#7")
    assert "Ticket :" not in corps and "ouverte" not in corps
    assert "## Release notes" not in _pr_body(item, {}), "pas de section vide"
