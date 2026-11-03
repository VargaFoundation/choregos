"""Helpers for the `github-software-delivery` template tests: a dev project, work items, scripted agents."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..conftest import Fixture, stage_result
from .train_and_work_item import wait_until_state

REPOSITORY = "varga/sandbox"


PROPOSED_ADR = """+---
+status: "proposed"
+---
+# Use a message queue for invoices
+
+## Context and Problem Statement
+
+Invoices are sent synchronously (src/billing/send.py:42) and time out under load.
+
+## Considered Options
+
+* A message queue
+* A retry loop
+
+## Decision Outcome
+
+Chosen option: "A message queue", because it absorbs the bursts measured in the research.
"""


def scripted_agents(results: dict[str, Callable[[], Any]]) -> Callable[[Any], Any]:
    """The fake executor returns, for each transition, the result its agent would return."""

    def play(stage_input: Any) -> Any:
        factory = results.get(stage_input.transition.id)
        assert factory is not None, f"no scripted result for {stage_input.transition.id}"
        return factory()

    return play


async def report_ci_result(setup: Fixture, ticket: Any, conclusion: str, delivery: str) -> None:
    """The PR exists; GitHub Actions returns its verdict, and the webhook carries it to the work item."""
    for _ in range(600):
        if setup.adapters.scm.prs:
            break
        await asyncio.sleep(0.1)
    pr = next(iter(setup.adapters.scm.prs.values())).ref
    setup.adapters.scm.set_checks(pr, conclusion, scanners=False)
    payload = {"conclusion": conclusion}
    event = {
        "type": "scm.check.completed",
        "source": "github",
        "delivery_id": delivery,
        "payload": payload,
    }
    await ticket.signal("inbound", event)


async def decide(ticket: Any, approved: bool = True, reason: str = "") -> None:
    await wait_until_state(ticket, "status", lambda s: bool(s["pending_request"]))
    decision = {"approved": approved, "decided_by": "vincent", "kind": "approval", "reason": reason}
    await ticket.signal("human_decision", decision)
    await wait_until_state(ticket, "status", lambda s: not s["pending_request"])


async def requests_of_item(item_id: str) -> list[str]:
    from choregos_api.db.models import HumanRequest
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(HumanRequest)
                .where(HumanRequest.work_item_id == item_id)
                .order_by(HumanRequest.requested_at)
            )
        ).scalars()
        return [str(r.transition_id) for r in rows]


async def start_interpreter(env: Any, setup: Fixture, project_id: str, item_id: str, key: str) -> Any:
    from choregos_api.temporal import interpreter_id

    return await env.client.start_workflow(
        "WorkflowInterpreter",
        {"project_id": project_id, "project_slug": "dev", "work_item_id": item_id, "tracker_key": key},
        id=interpreter_id("dev", key),
        task_queue="test",
    )


async def wait_for_state(ticket: Any, state: str) -> None:
    await wait_until_state(ticket, "status", lambda s: s["state"] == state)


def study_results(adr: str) -> dict[str, Callable[[], Any]]:
    return {
        "t-frame": lambda: stage_result("question", outputs={"spec_markdown": "Which queue?"}),
        "t-research": lambda: stage_result("options", outputs={"research_markdown": "Two options."}),
        "t-draft": lambda: stage_result(
            "ADR drafted", outputs={"adr_path": "docs/adr/0001-queue.md", "adr_markdown": adr}
        ),
        "t-critique": verdict_result("critique", review_markdown="Sound."),
        "t-accept": lambda: stage_result("ADR accepted"),
    }


async def create_dev_project(setup: Fixture) -> str:
    """A second project of the organisation, born from the template: its workflows, policy and agents."""
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope
    from choregos_api.services import ensure_defaults
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        project = Project(
            org_id=org.id,
            slug="dev",
            name="Development",
            status="active",
            template_ref="github-software-delivery@1.0.0",
            config={
                "slug": "dev",
                "org": "varga",
                "repo": {"url": f"https://github.com/{REPOSITORY}.git", "default_branch": "main"},
                "notify": {"slack_channel": "#dev"},
            },
        )
        session.add(project)
        await session.flush()
        await ensure_defaults(session, project)
        project_id = str(project.id)
    setup.adapters.cd.set_health("dev", "Healthy")
    return project_id


async def releases_of(env: str) -> list[Any]:
    from choregos_api.db.models import Release
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return list((await session.execute(select(Release).where(Release.env == env))).scalars())


async def runs_of(item_id: str) -> list[tuple[str, str]]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            await session.execute(select(Run).where(Run.work_item_id == item_id).order_by(Run.created_at))
        ).scalars()
        return [(str(r.transition_id), str(r.agent_slug)) for r in rows]


async def create_ticket(setup: Fixture, project_id: str, number: int, workflow: str) -> tuple[str, str]:
    from choregos_api.db.models import WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_core.domain import WorkItemData
    from sqlalchemy import select

    key = f"{REPOSITORY}#{number}"
    async with session_scope() as session:
        definition = (
            await session.execute(
                select(WorkflowDef).where(
                    WorkflowDef.project_id == project_id,
                    WorkflowDef.name == workflow,
                    WorkflowDef.is_active.is_(True),
                )
            )
        ).scalar_one()
        item = WorkItem(
            project_id=project_id,
            tracker_key=key,
            title=f"ticket {number}",
            body_snapshot="The invoice total ignores credits.",
            state="inbox",
            workflow_def_id=definition.id,
        )
        session.add(item)
        await session.flush()
        item_id = str(item.id)
    setup.adapters.tracker.items[key] = WorkItemData(key=key, title=f"ticket {number}", state="Todo")
    return item_id, key


async def start_requested_train(env: Any, name: str) -> tuple[Any, dict[str, Any]]:
    """The train the work item asked for (an in-memory signal under the fakes), started for real."""
    from choregos_orchestrator.train_client import fake_signals

    train_id = f"train-dev-{name}"
    for _ in range(600):
        if fake_signals(train_id):
            break
        await asyncio.sleep(0.1)
    ((signal, boarding),) = fake_signals(train_id)
    assert signal == "merged"
    handle = await env.client.start_workflow(
        "ReleaseTrain", {"project_slug": "dev", "env": name}, id=train_id, task_queue="test"
    )
    payload = {k: v for k, v in boarding.items() if k != "start_payload"}
    await handle.signal("merged", payload)
    await handle.signal("depart_now", {"by": "vincent"})
    return handle, payload


def verdict_result(name: str, verdict: str = "approve", **outputs_extra: Any) -> Callable[[], Any]:
    return lambda: stage_result(f"{name}: {verdict}", outputs={"verdict": verdict, **outputs_extra})


def prod_verifier_result() -> Any:
    from choregos_contracts import Evidence

    proof = Evidence(tests_passed=True, tests_run=3, facts={"smoke_ok": True})
    return stage_result("go: error rate unchanged", outputs={"verdict": "approve"}, evidence=proof)
