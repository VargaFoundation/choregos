"""The `software-delivery-governed` template traversed in fakes mode (S33-07), with a real `ReleaseTrain`.

One ticket goes from the issue to production verified: research, a decision record the architects
approve, specification, plan, implementation, QA, security review, supply-chain scan, a compliance
review that people decide, documentation, release readiness, a pull request a maintainer approves,
staging, production under the captain, verification. Every agent run names the catalogue agent the
project received when it was born from the template (ADR 0040 §5).

"One ticket traverses it on the dev" (the backlog's acceptance) is a run on the dev tenant with real
agents: not done here, and not claimed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from .conftest import Fixture, stage_result
from .test_gabarit_livraison_logicielle import (
    DEPOT,
    _agents,
    _ci,
    _decider,
    _demandes,
    _demarrer,
    _etat,
    _jusqu_a,
    _releases,
    _runs,
    _ticket,
    _train,
    _verdict,
)

pytestmark = pytest.mark.asyncio


async def _governed_project(setup: Fixture) -> str:
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_api.services import ensure_defaults
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        project = Project(
            org_id=org.id,
            slug="dev",
            name="Governed development",
            status="active",
            template_ref="software-delivery-governed@1.0.0",
            config={
                "slug": "dev",
                "org": "varga",
                "repo": {"url": f"https://github.com/{DEPOT}.git", "default_branch": "main"},
                "notify": {"slack_channel": "#dev"},
            },
        )
        session.add(project)
        await session.flush()
        await ensure_defaults(session, project)
        project_id = str(project.id)
    setup.adapters.cd.set_health("dev", "Healthy")
    return project_id


def _devops_verification() -> Any:
    return stage_result(
        "go: error rate 0.2 % before and after",
        outputs={"devops_report": "Loki: no new errors.", "verdict": "approve"},
    )


async def test_one_ticket_traverses_the_governed_workflow_to_production(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    project_id = await _governed_project(setup)
    item_id, key = await _ticket(setup, project_id, 21, "governed-delivery")
    setup.adapters.scm.set_diff(
        DEPOT, "main", "choregos/21", [("src/billing/queue.py", 80, 2), ("docs/billing.md", 12, 0)]
    )
    setup.adapters.executor.handler = _agents(
        {
            "t-triage": lambda: stage_result("medium", outputs={"size": "M", "risk": "medium"}),
            "t-study": lambda: stage_result("options", outputs={"research_markdown": "A queue or a cron."}),
            "t-architecture": lambda: stage_result(
                "ADR drafted", outputs={"adr_path": "docs/adr/0001-queue.md", "adr_markdown": "# Use a queue"}
            ),
            "t-specify": lambda: stage_result(
                "spec",
                outputs={"spec_markdown": "## Spec", "allowed_paths": ["src/**", "tests/**", "docs/**"]},
            ),
            "t-plan": lambda: stage_result("plan", outputs={"plan_markdown": "1. queue"}),
            "t-implement": lambda: stage_result("done", artifacts={"branch": "choregos/21"}),
            "t-qa": lambda: stage_result(
                "QA",
                outputs={
                    "test_plan": "boundaries, empty batch, two senders at once",
                    "mutation_report": "mutmut: 41 killed, 0 survived",
                    "test_report": "430 passed",
                },
            ),
            "t-security-review": _verdict("security", security_review_markdown="No exposure."),
            "t-supply-chain": _verdict("secops", secops_report="trivy: 0 HIGH", sarif='{"version": "2.1.0"}'),
            "t-compliance": _verdict(
                "compliance", compliance_review="This review is not legal advice. MIT dependency only."
            ),
            "t-document": lambda: stage_result("docs", outputs={"docs_report": "billing.md updated"}),
            "t-release-readiness": _verdict("readiness", rollout_plan="Canary 10 %, rollback on 5xx > 1 %."),
            "t-verify": _devops_verification,
        }
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await _demarrer(temporal_env, setup, project_id, item_id, key)
            await _etat(ticket, "awaiting_architecture_approval")
            await _decider(ticket)
            await _etat(ticket, "awaiting_compliance_decision")
            await _decider(ticket)
            await _etat(ticket, "awaiting_pr_approval")
            await _decider(ticket)
            await _ci(setup, ticket, "success", "ci-21")
            staging, _ = await _train(temporal_env, "staging")
            await _etat(ticket, "staged")
            prod, boarding = await _train(temporal_env, "prod")
            assert boarding["approval"] == {"required": True, "group": "release-captains"}
            await _jusqu_a(prod, "status_query", lambda s: s["status"] == "awaiting_approval")
            assert (await ticket.query("status"))["state"] == "staged", "production waits for its captain"
            await prod.signal("approve", {"by": "marie"})
            outcome = await asyncio.wait_for(ticket.result(), timeout=120)
            for train in (staging, prod):
                await train.signal("abort", {"by": "test"})
                await train.result()

    assert outcome["state"] == "verified"
    assert await _demandes(item_id) == ["t-approve-architecture", "t-decide-compliance", "t-approve-pr"]
    (release,) = await _releases("prod")
    assert release.approved_by == "marie", "production went through the train, under the captain"
    assert [agent for _, agent in await _runs(item_id)] == [
        "triager",
        "researcher",
        "architect",
        "spec-writer",
        "planner",
        "developer",
        "qa",
        "security-reviewer",
        "secops",
        "compliance",
        "docs-writer",
        "devops",
        "devops",
    ]


async def test_a_compliance_rejection_sends_the_work_back_with_its_reason(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """The compliance agent recommends; people decide, and a refusal goes to the review responder."""
    project_id = await _governed_project(setup)
    item_id, key = await _ticket(setup, project_id, 22, "governed-delivery")
    setup.adapters.scm.set_diff(DEPOT, "main", "choregos/22", [("src/billing/export.py", 40, 0)])
    results = {
        "t-triage": lambda: stage_result("medium", outputs={"size": "M", "risk": "medium"}),
        "t-study": lambda: stage_result("options", outputs={"research_markdown": "Export as CSV."}),
        "t-architecture": lambda: stage_result(
            "ADR", outputs={"adr_path": "docs/adr/0002-export.md", "adr_markdown": "# Export"}
        ),
        "t-specify": lambda: stage_result(
            "spec", outputs={"spec_markdown": "## Spec", "allowed_paths": ["src/**"]}
        ),
        "t-plan": lambda: stage_result("plan", outputs={"plan_markdown": "1. export"}),
        "t-implement": lambda: stage_result("done", artifacts={"branch": "choregos/22"}),
        "t-qa": lambda: stage_result(
            "QA",
            outputs={"test_plan": "empty export", "mutation_report": "manual mutation", "test_report": "ok"},
        ),
        "t-security-review": _verdict("security", security_review_markdown="Fine."),
        "t-supply-chain": _verdict("secops", secops_report="clean", sarif="{}"),
        "t-compliance": _verdict(
            "compliance",
            "request_changes",
            compliance_review="Exports e-mail addresses with no stated purpose.",
        ),
        "t-address-review": lambda: stage_result("answered", outputs={"review_markdown": "Purpose stated."}),
    }
    setup.adapters.executor.handler = _agents(results)
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            ticket = await _demarrer(temporal_env, setup, project_id, item_id, key)
            await _etat(ticket, "awaiting_architecture_approval")
            await _decider(ticket)
            await _etat(ticket, "awaiting_compliance_decision")
            await _decider(ticket, approuve=False, raison="No purpose for the e-mail addresses.")
            await _etat(ticket, "awaiting_compliance_decision")
            await ticket.cancel()

    transitions = [t for t, _ in await _runs(item_id)]
    assert "t-address-review" in transitions, transitions
    assert transitions.count("t-compliance") == 2, "the compliance review ran again on what changed"
