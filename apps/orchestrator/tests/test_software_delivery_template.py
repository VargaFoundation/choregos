"""The `github-software-delivery` template walked through (S21-23), on fakes, with a REAL `ReleaseTrain`.

`dev-simple` goes from the work item to verified production without a human; `dev-complex` waits for
its three human decisions, plus the train captain; `study` merges a MADR 4 ADR and goes nowhere; an
ADR without a decision does not pass. The agents that play are the catalogue's, installed at the
project's birth: each run names its agent.
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


async def test_dev_simple_goes_from_the_work_item_to_verified_production_without_a_human(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    project_id = await create_dev_project(setup)
    item_id, tracker_key = await create_ticket(setup, project_id, 7, "dev-simple")
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
            ticket = await start_interpreter(temporal_env, setup, project_id, item_id, tracker_key)
            await wait_for_state(ticket, "in_pr")
            await report_ci_result(setup, ticket, "success", "ci-7")
            train, boarding = await start_requested_train(temporal_env, "prod")
            assert boarding["approval"] == {"required": False, "group": None}
            outcome = await asyncio.wait_for(ticket.result(), timeout=90)
            await train.signal("abort", {"by": "test"})
            await train.result()

    assert outcome["state"] == "verified"
    assert await requests_of_item(item_id) == [], "no human decision"
    (release,) = await releases_of("prod")
    assert release.status == "done" and release.approved_by is None, "the train left alone"
    assert [agent for _, agent in await runs_of(item_id)] == [
        "triager",
        "developer",
        "tester",
        "reviewer",
        "prod-verifier",
    ]


async def test_dev_simple_stops_a_work_item_too_big_at_triage(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """An L work item is not a small fix: a human migrates it to `dev-complex`."""
    project_id = await create_dev_project(setup)
    item_id, tracker_key = await create_ticket(setup, project_id, 8, "dev-simple")
    setup.adapters.executor.handler = scripted_agents(
        {"t-triage": lambda: stage_result("big", outputs={"size": "L", "risk": "medium"})}
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await start_interpreter(temporal_env, setup, project_id, item_id, tracker_key)
            await wait_for_state(ticket, "needs_human")
            await ticket.cancel()
    assert await runs_of(item_id) == [("t-triage", "triager")], "a single triage, no development"


# ───────────────────────── dev-complex ─────────────────────────


async def test_dev_complex_waits_for_three_human_decisions_and_the_captain(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """The spec, the PR, and — the CI having turned red AFTER the approval, the fixer having pushed —
    the PR again: what changed was not seen. Then staging without approval, prod under the captain."""
    project_id = await create_dev_project(setup)
    item_id, tracker_key = await create_ticket(setup, project_id, 9, "dev-complex")
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
            ticket = await start_interpreter(temporal_env, setup, project_id, item_id, tracker_key)
            await wait_for_state(ticket, "awaiting_spec_approval")
            await decide(ticket)
            await wait_for_state(ticket, "awaiting_pr_approval")
            await decide(ticket)
            await report_ci_result(setup, ticket, "failure", "ci-9-red")
            await wait_for_state(ticket, "awaiting_pr_approval")
            await wait_until_state(ticket, "status", lambda s: bool(s["pending_request"]))
            await report_ci_result(setup, ticket, "success", "ci-9-green")
            await decide(ticket)

            staging, boarding = await start_requested_train(temporal_env, "staging")
            assert boarding["approval"] == {"required": False, "group": None}
            await wait_for_state(ticket, "staged")
            prod, boarding = await start_requested_train(temporal_env, "prod")
            assert boarding["approval"] == {"required": True, "group": "release-captains"}
            await wait_until_state(prod, "status_query", lambda s: s["status"] == "awaiting_approval")
            assert (await ticket.query("status"))["state"] == "staged", "prod waits for its captain"
            await prod.signal("approve", {"by": "marie"})
            outcome = await asyncio.wait_for(ticket.result(), timeout=90)
            for train in (staging, prod):
                await train.signal("abort", {"by": "test"})
                await train.result()

    assert outcome["state"] == "verified"
    assert await requests_of_item(item_id) == ["t-approve-spec", "t-approve-pr", "t-approve-pr"]
    (release,) = await releases_of("prod")
    assert release.approved_by == "marie"
    (staging_release,) = await releases_of("staging")
    assert staging_release.approved_by is None
    transitions = [t for t, _ in await runs_of(item_id)]
    assert transitions.count("t-fix-ci") == 1, transitions
    assert "t-document" in transitions, transitions
    body = next(iter(setup.adapters.scm.prs.values())).body
    assert "## Documentation\n- docs/billing/queue.md" in body, "the documentation report is in the PR"
    assert "## Release notes\n- Invoices are queued." in body, "the release notes are in the PR"


async def test_dev_complex_without_documentation_waits_for_a_person(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """A change that touches code and no documentation never reaches the review: `docs_updated` sends
    it back to the writer twice, then to a person (S39-15)."""
    project_id = await create_dev_project(setup)
    item_id, tracker_key = await create_ticket(setup, project_id, 15, "dev-complex")
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
            ticket = await start_interpreter(temporal_env, setup, project_id, item_id, tracker_key)
            await wait_for_state(ticket, "awaiting_spec_approval")
            await decide(ticket)
            await wait_for_state(ticket, "needs_human")
            await ticket.cancel()
    transitions = [t for t, _ in await runs_of(item_id)]
    assert transitions.count("t-document") == 2, transitions
    assert "t-review" not in transitions, transitions


# ───────────────────────── study ─────────────────────────


async def test_study_merges_an_approved_madr_adr_and_goes_nowhere(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_orchestrator.train_client import fake_signals

    project_id = await create_dev_project(setup)
    item_id, tracker_key = await create_ticket(setup, project_id, 11, "study")
    adr = ("docs/adr/0001-queue.md", 17, 0, PROPOSED_ADR)
    setup.adapters.scm.set_diff(REPOSITORY, "main", "choregos/11", [adr])
    setup.adapters.executor.handler = scripted_agents(study_results(PROPOSED_ADR))
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await start_interpreter(temporal_env, setup, project_id, item_id, tracker_key)
            await wait_for_state(ticket, "awaiting_decision")
            # The architect will set the status to `accepted`: the branch will carry it.
            accepted = PROPOSED_ADR.replace('status: "proposed"', 'status: "accepted"\n+date: 2026-10-07')
            setup.adapters.scm.set_diff(REPOSITORY, "main", "choregos/11", [(adr[0], 18, 0, accepted)])
            await decide(ticket)
            await wait_for_state(ticket, "in_pr")
            await report_ci_result(setup, ticket, "success", "ci-11")
            outcome = await asyncio.wait_for(ticket.result(), timeout=90)

    assert outcome["state"] == "adopted"
    assert not [w for w in fake_signals() if w.startswith("train-dev")], "a study boards no train"
    assert await requests_of_item(item_id) == ["t-decide"]
    assert [a for _, a in await runs_of(item_id)] == [
        "spec-writer",
        "researcher",
        "architect",
        "reviewer",
        "architect",
    ]


async def test_study_renumbers_an_adr_whose_number_was_taken_before_the_merge(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """#287: another study merges its 0001 while ours waits for the architects' decision.
    The merge refuses the duplicate (`adr_number_free`) and hands the study back to the architect,
    who renumbers; the PR then merges, once."""
    project_id = await create_dev_project(setup)
    item_id, tracker_key = await create_ticket(setup, project_id, 13, "study")
    scm = setup.adapters.scm
    accepted = PROPOSED_ADR.replace('status: "proposed"', 'status: "accepted"\n+date: 2026-10-10')
    scm.set_diff(REPOSITORY, "main", "choregos/13", [("docs/adr/0001-queue.md", 17, 0, PROPOSED_ADR)])
    acceptances: list[str] = []

    def accept() -> Any:
        # First time: the status becomes `accepted`. Second: the architect renumbers to 0002.
        path = "docs/adr/0001-queue.md" if not acceptances else "docs/adr/0002-queue.md"
        acceptances.append(path)
        scm.set_diff(REPOSITORY, "main", "choregos/13", [(path, 18, 0, accepted)])
        return stage_result("ADR accepted")

    setup.adapters.executor.handler = scripted_agents({**study_results(PROPOSED_ADR), "t-accept": accept})
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await start_interpreter(temporal_env, setup, project_id, item_id, tracker_key)
            await wait_for_state(ticket, "awaiting_decision")
            await scm.commit_files(
                REPOSITORY, "main", {"docs/adr/0001-cache.md": "# Cache\n"}, "another study"
            )
            await decide(ticket)
            await wait_for_state(ticket, "in_pr")
            await report_ci_result(setup, ticket, "success", "ci-13")
            outcome = await asyncio.wait_for(ticket.result(), timeout=90)

    assert outcome["state"] == "adopted"
    assert acceptances == ["docs/adr/0001-queue.md", "docs/adr/0002-queue.md"], "renumbered, once"
    assert [t for t, _ in await runs_of(item_id)][-2:] == ["t-accept", "t-accept"]
    assert len(scm.merge_queue) == 1, "merged only once, after the renumbering"


async def test_an_adr_without_a_decision_outcome_does_not_pass(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    project_id = await create_dev_project(setup)
    item_id, tracker_key = await create_ticket(setup, project_id, 12, "study")
    # The decision sentence is there, not its section: the MADR 4 template is missing, not the idea.
    without_decision = PROPOSED_ADR.replace("+## Decision Outcome\n+\n", "")
    assert 'Chosen option: "' in without_decision and "Decision Outcome" not in without_decision
    setup.adapters.scm.set_diff(
        REPOSITORY, "main", "choregos/12", [("docs/adr/0001-queue.md", 13, 0, without_decision)]
    )
    setup.adapters.executor.handler = scripted_agents(study_results(without_decision))
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await start_interpreter(temporal_env, setup, project_id, item_id, tracker_key)
            await wait_for_state(ticket, "needs_human")
            await ticket.cancel()
    runs = [t for t, _ in await runs_of(item_id)]
    assert runs.count("t-draft") == 3 and "t-critique" not in runs, runs
