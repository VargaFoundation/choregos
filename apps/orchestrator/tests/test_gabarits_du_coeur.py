"""Les gabarits du cœur traversés là où ils cassaient (S21-26).

`full-auto` et `advanced` portaient leur réparateur de CI depuis `pr_open`, où il n'était choisi
que sur un résultat que l'interpréteur avait déjà oublié : une CI rouge finissait chez un humain
(#284). Les notes de version d'`advanced` étaient une boucle `merged → merged` jamais choisie
(#285), et `provenance_signed` attendait pour toujours (#283).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from .conftest import Fixture, stage_result

pytestmark = pytest.mark.asyncio


async def _epingler(setup: Fixture, nom: str) -> None:
    """Publie le gabarit du cœur `nom` dans le projet et y épingle le ticket du décor."""
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import SYSTEM
    from choregos_api.services.definitions import publier_workflow
    from choregos_core.dsl import template_yaml

    async with session_scope() as session:
        projet = await session.get(Project, setup.project_id)
        assert projet is not None
        definition = await publier_workflow(session, SYSTEM, projet, template_yaml(nom))
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id = definition.id


def _agents(resultats: dict[str, Callable[[], Any]]) -> Callable[[Any], Any]:
    def jouer(stage_input: Any) -> Any:
        fabrique = resultats.get(stage_input.transition.id)
        assert fabrique is not None, f"aucun résultat scripté pour {stage_input.transition.id}"
        return fabrique()

    return jouer


async def _demarrer(env: Any, setup: Fixture) -> Any:
    return await env.client.start_workflow(
        "WorkflowInterpreter",
        {
            "project_id": setup.project_id,
            "project_slug": setup.project_slug,
            "work_item_id": setup.work_item_id,
            "tracker_key": setup.tracker_key,
        },
        id="wi-billing-api-gabarit",
        task_queue="test",
    )


async def _etat(ticket: Any, etat: str, timeout: float = 60.0) -> None:
    for _ in range(int(timeout * 10)):
        if (await ticket.query("status"))["state"] == etat:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"jamais en `{etat}` : {await ticket.query('status')}")


async def _ci(setup: Fixture, ticket: Any, conclusion: str, livraison: str) -> None:
    for _ in range(600):
        if setup.adapters.scm.prs:
            break
        await asyncio.sleep(0.1)
    pr = next(iter(setup.adapters.scm.prs.values())).ref
    setup.adapters.scm.set_checks(pr, conclusion)
    charge = {"conclusion": conclusion}
    evenement = {
        "type": "scm.check.completed",
        "source": "github",
        "delivery_id": livraison,
        "payload": charge,
    }
    await ticket.signal("inbound", evenement)


async def _transitions_jouees(setup: Fixture) -> list[str]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        lignes = (
            await session.execute(
                select(Run).where(Run.work_item_id == setup.work_item_id).order_by(Run.created_at)
            )
        ).scalars()
        return [str(r.transition_id) for r in lignes]


def _pousser(setup: Fixture) -> Any:
    """Le réparateur pousse : comme sur GitHub, les checks du nouveau commit repartent en attente."""
    for pr in setup.adapters.scm.prs.values():
        pr.checks = []
    return stage_result("flaky import fixed", artifacts={"branch": "choregos/123"})


def _faits(**sorties: Any) -> Callable[[], Any]:
    return lambda: stage_result("done", outputs=sorties)


async def test_full_auto_une_ci_rouge_passe_par_le_reparateur_puis_fusionne(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    await _epingler(setup, "full-auto")
    setup.adapters.executor.handler = _agents(
        {
            "t-triage": _faits(size="M", risk="low"),
            "t-refine": _faits(spec_markdown="## Spec", allowed_paths=["src/orders/**", "tests/orders/**"]),
            "t-implement": lambda: stage_result("done", artifacts={"branch": "choregos/123"}),
            "t-verify": _faits(),
            "t-review": _faits(review_markdown="Fine.", verdict="approve"),
            "t-fix-ci": lambda: _pousser(setup),
        }
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await _demarrer(temporal_env, setup)
            await _etat(ticket, "pr_open")
            await _ci(setup, ticket, "failure", "ci-rouge")
            for _ in range(600):
                if "t-fix-ci" in await _transitions_jouees(setup):
                    break
                await asyncio.sleep(0.1)
            await _etat(ticket, "pr_open")
            await _ci(setup, ticket, "success", "ci-verte")
            await _etat(ticket, "merged")
            await ticket.cancel()
    jouees = await _transitions_jouees(setup)
    assert jouees.count("t-fix-ci") == 1, jouees
    assert setup.adapters.scm.merge_queue, "la PR est partie en fusion après la réparation"


async def test_advanced_ecrit_ses_notes_de_version_avant_la_pr_et_fusionne_sans_provenance(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    await _epingler(setup, "advanced")
    setup.adapters.executor.handler = _agents(
        {
            "t-triage": _faits(size="M", risk="low"),
            "t-refine": _faits(spec_markdown="## Spec", allowed_paths=["src/orders/**", "tests/orders/**"]),
            "t-plan": _faits(plan_markdown="1. credits"),
            "t-implement": lambda: stage_result("done", artifacts={"branch": "choregos/123"}),
            "t-verify": _faits(),
            "t-agent-review": _faits(review_markdown="Fine.", verdict="approve"),
            "t-release-notes": _faits(release_notes_markdown="- Credits are deducted from the total."),
        }
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await _demarrer(temporal_env, setup)
            await _etat(ticket, "awaiting_spec_approval")
            await ticket.signal(
                "human_decision", {"approved": True, "decided_by": "marie", "kind": "approval"}
            )
            await _etat(ticket, "pr_open")
            pr = next(iter(setup.adapters.scm.prs.values()))
            assert "## Release notes\n- Credits are deducted from the total." in pr.body
            setup.adapters.scm.submit_review(pr.ref, "marie", "approved")
            await _ci(setup, ticket, "success", "ci-verte")
            await _etat(ticket, "merged")
            await ticket.cancel()
    jouees = await _transitions_jouees(setup)
    assert jouees.index("t-release-notes") < len(jouees), jouees
    assert setup.adapters.scm.merge_queue, "aucune garantie n'attend une provenance que rien ne fournit"
