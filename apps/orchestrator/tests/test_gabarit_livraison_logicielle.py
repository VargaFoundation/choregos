"""Le gabarit `github-software-delivery` traversé (S21-23), sur les faux, avec un VRAI `ReleaseTrain`.

`dev-simple` va du ticket à la production vérifiée sans humain ; `dev-complex` attend ses trois
décisions humaines, plus le capitaine du train ; `study` fusionne un ADR MADR 4 et ne part nulle part ;
un ADR sans décision ne passe pas. Les agents qui jouent sont ceux du catalogue, installés à la
naissance du projet : chaque run nomme son agent.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from .conftest import Fixture, stage_result
from .kits.software_delivery import (
    PROPOSED_ADR,
    REPOSITORY,
    create_dev_project,
    create_ticket,
    decide,
    prod_verifier_result,
    releases_of,
    report_ci_result,
    requests_of_item,
    runs_of,
    scripted_agents,
    start_interpreter,
    start_requested_train,
    study_results,
    verdict_result,
    wait_for_state,
)
from .kits.train_and_work_item import wait_until_state

pytestmark = pytest.mark.asyncio

# ───────────────────────── dev-simple ─────────────────────────


async def test_dev_simple_va_du_ticket_a_la_production_verifiee_sans_humain(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    projet_id = await create_dev_project(setup)
    item_id, cle = await create_ticket(setup, projet_id, 7, "dev-simple")
    setup.adapters.scm.set_diff(REPOSITORY, "main", "choregos/7", [("src/billing/total.py", 12, 3)])
    setup.adapters.executor.handler = scripted_agents(
        {
            "t-triage": lambda: stage_result("small", outputs={"size": "S", "risk": "low"}),
            "t-implement": lambda: stage_result("fixed", artifacts={"branch": "choregos/7"}),
            "t-test": lambda: stage_result("tests pass", outputs={"test_report": "412 passed"}),
            "t-review": verdict_result("review", review_markdown="Looks right."),
            "t-verify-prod": prod_verifier_result,
        }
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await start_interpreter(temporal_env, setup, projet_id, item_id, cle)
            await wait_for_state(ticket, "in_pr")
            await report_ci_result(setup, ticket, "success", "ci-7")
            train, embarquement = await start_requested_train(temporal_env, "prod")
            assert embarquement["approval"] == {"required": False, "group": None}
            issue = await asyncio.wait_for(ticket.result(), timeout=90)
            await train.signal("abort", {"by": "test"})
            await train.result()

    assert issue["state"] == "verified"
    assert await requests_of_item(item_id) == [], "no human decision"
    (release,) = await releases_of("prod")
    assert release.status == "done" and release.approved_by is None, "le train est parti seul"
    assert [agent for _, agent in await runs_of(item_id)] == [
        "triager",
        "developer",
        "tester",
        "reviewer",
        "prod-verifier",
    ]


async def test_dev_simple_arrete_un_ticket_trop_gros_au_triage(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Un ticket L n'est pas un petit correctif : un humain le migre vers `dev-complex`."""
    projet_id = await create_dev_project(setup)
    item_id, cle = await create_ticket(setup, projet_id, 8, "dev-simple")
    setup.adapters.executor.handler = scripted_agents(
        {"t-triage": lambda: stage_result("big", outputs={"size": "L", "risk": "medium"})}
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await start_interpreter(temporal_env, setup, projet_id, item_id, cle)
            await wait_for_state(ticket, "needs_human")
            await ticket.cancel()
    assert await runs_of(item_id) == [("t-triage", "triager")], "a single triage, no development"


# ───────────────────────── dev-complex ─────────────────────────


async def test_dev_complex_attend_trois_decisions_humaines_et_le_capitaine(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """La spec, la PR, et — la CI ayant rougi APRÈS l'approbation, le réparateur ayant poussé — la PR
    encore : ce qui a changé n'a pas été vu. Puis staging sans approbation, la prod sous le capitaine."""
    projet_id = await create_dev_project(setup)
    item_id, cle = await create_ticket(setup, projet_id, 9, "dev-complex")
    setup.adapters.scm.set_diff(
        REPOSITORY,
        "main",
        "choregos/9",
        [
            ("src/billing/queue.py", 120, 4),
            ("docs/billing/queue.md", 14, 0, "@@ -0,0 +1 @@\n+Invoices are queued."),
            ("CHANGELOG.md", 1, 0, "@@ -0,0 +1 @@\n+- Invoices are queued."),
        ],
    )
    setup.adapters.executor.handler = scripted_agents(
        {
            "t-triage": lambda: stage_result("medium", outputs={"size": "M", "risk": "medium"}),
            "t-specify": lambda: stage_result(
                "spec",
                outputs={
                    "spec_markdown": "## Spec",
                    "allowed_paths": ["src/**", "tests/**", "docs/**", "CHANGELOG.md"],
                },
            ),
            "t-plan": lambda: stage_result("plan", outputs={"plan_markdown": "1. queue"}),
            "t-implement": lambda: stage_result("done", artifacts={"branch": "choregos/9"}),
            "t-test": lambda: stage_result("tests pass", outputs={"test_report": "420 passed"}),
            "t-document": lambda: stage_result(
                "documented",
                outputs={"docs_report": "- docs/billing/queue.md: queued sending"},
                artifacts={"branch": "choregos/9"},
            ),
            "t-review": verdict_result("review", review_markdown="Fine."),
            "t-security-review": verdict_result("security", security_review_markdown="No exposure."),
            "t-release-notes": lambda: stage_result(
                "notes", outputs={"release_notes_markdown": "- Invoices are queued."}
            ),
            "t-fix-ci": lambda: stage_result("flaky import fixed", artifacts={"branch": "choregos/9"}),
            "t-verify-prod": prod_verifier_result,
        }
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await start_interpreter(temporal_env, setup, projet_id, item_id, cle)
            await wait_for_state(ticket, "awaiting_spec_approval")
            await decide(ticket)
            await wait_for_state(ticket, "awaiting_pr_approval")
            await decide(ticket)
            await report_ci_result(setup, ticket, "failure", "ci-9-rouge")
            await wait_for_state(ticket, "awaiting_pr_approval")
            await wait_until_state(ticket, "status", lambda s: bool(s["pending_request"]))
            await report_ci_result(setup, ticket, "success", "ci-9-vert")
            await decide(ticket)

            staging, embarquement = await start_requested_train(temporal_env, "staging")
            assert embarquement["approval"] == {"required": False, "group": None}
            await wait_for_state(ticket, "staged")
            prod, embarquement = await start_requested_train(temporal_env, "prod")
            assert embarquement["approval"] == {"required": True, "group": "release-captains"}
            await wait_until_state(prod, "status_query", lambda s: s["status"] == "awaiting_approval")
            assert (await ticket.query("status"))["state"] == "staged", "la prod attend son capitaine"
            await prod.signal("approve", {"by": "marie"})
            issue = await asyncio.wait_for(ticket.result(), timeout=90)
            for train in (staging, prod):
                await train.signal("abort", {"by": "test"})
                await train.result()

    assert issue["state"] == "verified"
    assert await requests_of_item(item_id) == ["t-approve-spec", "t-approve-pr", "t-approve-pr"]
    (release,) = await releases_of("prod")
    assert release.approved_by == "marie"
    (staging_release,) = await releases_of("staging")
    assert staging_release.approved_by is None
    transitions = [t for t, _ in await runs_of(item_id)]
    assert transitions.count("t-fix-ci") == 1, transitions
    assert "t-document" in transitions, transitions
    corps = next(iter(setup.adapters.scm.prs.values())).body
    assert "## Documentation\n- docs/billing/queue.md" in corps, "the documentation report is in the PR"
    assert "## Release notes\n- Invoices are queued." in corps, "les notes de version sont dans la PR"


async def test_dev_complex_without_documentation_waits_for_a_person(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """A change that touches code and no documentation never reaches the review: `docs_updated` sends
    it back to the writer twice, then to a person (S39-15)."""
    projet_id = await create_dev_project(setup)
    item_id, cle = await create_ticket(setup, projet_id, 15, "dev-complex")
    setup.adapters.scm.set_diff(REPOSITORY, "main", "choregos/15", [("src/billing/queue.py", 120, 4)])
    setup.adapters.executor.handler = scripted_agents(
        {
            "t-triage": lambda: stage_result("medium", outputs={"size": "M", "risk": "medium"}),
            "t-specify": lambda: stage_result(
                "spec", outputs={"spec_markdown": "## Spec", "allowed_paths": ["src/**", "docs/**"]}
            ),
            "t-plan": lambda: stage_result("plan", outputs={"plan_markdown": "1. queue"}),
            "t-implement": lambda: stage_result("done", artifacts={"branch": "choregos/15"}),
            "t-test": lambda: stage_result("tests pass", outputs={"test_report": "420 passed"}),
            "t-document": lambda: stage_result(
                "nothing to say", outputs={"docs_report": "no documentation needed"}
            ),
            "t-review": verdict_result("review", review_markdown="Fine."),
        }
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await start_interpreter(temporal_env, setup, projet_id, item_id, cle)
            await wait_for_state(ticket, "awaiting_spec_approval")
            await decide(ticket)
            await wait_for_state(ticket, "needs_human")
            await ticket.cancel()
    transitions = [t for t, _ in await runs_of(item_id)]
    assert transitions.count("t-document") == 2, transitions
    assert "t-review" not in transitions, transitions


# ───────────────────────── study ─────────────────────────


async def test_study_fusionne_un_adr_madr_approuve_et_ne_part_nulle_part(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_orchestrator.train_client import fake_signals

    projet_id = await create_dev_project(setup)
    item_id, cle = await create_ticket(setup, projet_id, 11, "study")
    adr = ("docs/adr/0001-queue.md", 17, 0, PROPOSED_ADR)
    setup.adapters.scm.set_diff(REPOSITORY, "main", "choregos/11", [adr])
    setup.adapters.executor.handler = scripted_agents(study_results(PROPOSED_ADR))
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await start_interpreter(temporal_env, setup, projet_id, item_id, cle)
            await wait_for_state(ticket, "awaiting_decision")
            # L'architecte passera le statut à `accepted` : la branche le portera.
            accepte = PROPOSED_ADR.replace('status: "proposed"', 'status: "accepted"\n+date: 2026-10-07')
            setup.adapters.scm.set_diff(REPOSITORY, "main", "choregos/11", [(adr[0], 18, 0, accepte)])
            await decide(ticket)
            await wait_for_state(ticket, "in_pr")
            await report_ci_result(setup, ticket, "success", "ci-11")
            issue = await asyncio.wait_for(ticket.result(), timeout=90)

    assert issue["state"] == "adopted"
    assert not [w for w in fake_signals() if w.startswith("train-dev")], (
        "une étude n'embarque dans aucun train"
    )
    assert await requests_of_item(item_id) == ["t-decide"]
    assert [a for _, a in await runs_of(item_id)] == [
        "spec-writer",
        "researcher",
        "architect",
        "reviewer",
        "architect",
    ]


async def test_study_renumerote_un_adr_dont_le_numero_a_ete_pris_avant_la_fusion(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """#287 : une autre étude fusionne son 0001 pendant que la nôtre attend la décision des architectes.
    La fusion refuse le doublon (`adr_number_free`) et rend l'étude à l'architecte, qui renumérote ; la
    PR fusionne ensuite, une seule fois."""
    projet_id = await create_dev_project(setup)
    item_id, cle = await create_ticket(setup, projet_id, 13, "study")
    scm = setup.adapters.scm
    accepte = PROPOSED_ADR.replace('status: "proposed"', 'status: "accepted"\n+date: 2026-10-10')
    scm.set_diff(REPOSITORY, "main", "choregos/13", [("docs/adr/0001-queue.md", 17, 0, PROPOSED_ADR)])
    acceptations: list[str] = []

    def accepter() -> Any:
        # Première fois : le statut passe à `accepted`. Seconde : l'architecte renumérote en 0002.
        chemin = "docs/adr/0001-queue.md" if not acceptations else "docs/adr/0002-queue.md"
        acceptations.append(chemin)
        scm.set_diff(REPOSITORY, "main", "choregos/13", [(chemin, 18, 0, accepte)])
        return stage_result("ADR accepted")

    setup.adapters.executor.handler = scripted_agents({**study_results(PROPOSED_ADR), "t-accept": accepter})
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await start_interpreter(temporal_env, setup, projet_id, item_id, cle)
            await wait_for_state(ticket, "awaiting_decision")
            await scm.commit_files(
                REPOSITORY, "main", {"docs/adr/0001-cache.md": "# Cache\n"}, "another study"
            )
            await decide(ticket)
            await wait_for_state(ticket, "in_pr")
            await report_ci_result(setup, ticket, "success", "ci-13")
            issue = await asyncio.wait_for(ticket.result(), timeout=90)

    assert issue["state"] == "adopted"
    assert acceptations == ["docs/adr/0001-queue.md", "docs/adr/0002-queue.md"], "renuméroté, une fois"
    assert [t for t, _ in await runs_of(item_id)][-2:] == ["t-accept", "t-accept"]
    assert len(scm.merge_queue) == 1, "fusionnée une seule fois, après la renumérotation"


async def test_un_adr_sans_decision_outcome_ne_passe_pas(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    projet_id = await create_dev_project(setup)
    item_id, cle = await create_ticket(setup, projet_id, 12, "study")
    # La phrase de décision est là, pas sa section : c'est le gabarit MADR 4 qui manque, pas l'idée.
    sans_decision = PROPOSED_ADR.replace("+## Decision Outcome\n+\n", "")
    assert 'Chosen option: "' in sans_decision and "Decision Outcome" not in sans_decision
    setup.adapters.scm.set_diff(
        REPOSITORY, "main", "choregos/12", [("docs/adr/0001-queue.md", 13, 0, sans_decision)]
    )
    setup.adapters.executor.handler = scripted_agents(study_results(sans_decision))
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await start_interpreter(temporal_env, setup, projet_id, item_id, cle)
            await wait_for_state(ticket, "needs_human")
            await ticket.cancel()
    runs = [t for t, _ in await runs_of(item_id)]
    assert runs.count("t-draft") == 3 and "t-critique" not in runs, runs
