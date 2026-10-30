"""Helpers for the tests that drive a work item and its release train together."""

from __future__ import annotations

import asyncio
import copy
from typing import Any

from ..conftest import Fixture, stage_result


async def wait_until_state(handle: Any, query: str, predicate: Any, timeout: float = 60.0) -> dict[str, Any]:
    for _ in range(int(timeout * 10)):
        state = await handle.query(query)
        if predicate(state):
            return dict(state)
        await asyncio.sleep(0.1)
    raise AssertionError(f"never reached (last: {await handle.query(query)})")


async def run_work_item_to_train(setup: Fixture, temporal_env: Any) -> tuple[Any, dict[str, Any]]:
    """A `default-simple` work item driven up to the merge; returns its interpreter and its boarding."""
    from choregos_api.temporal import interpreter_id
    from choregos_orchestrator.train_client import fake_signals

    for result in (
        stage_result("spec", outputs={"size": "M", "risk": "low", "spec_markdown": "## Spec"}),
        stage_result(
            "implemented", artifacts={"branch": "choregos/123", "commits": ["fix(orders): credit notes"]}
        ),
        stage_result("verified"),
    ):
        setup.adapters.executor.queue_result(result)
    ticket = await temporal_env.client.start_workflow(
        "WorkflowInterpreter",
        {
            "project_id": setup.project_id,
            "project_slug": setup.project_slug,
            "work_item_id": setup.work_item_id,
            "tracker_key": setup.tracker_key,
        },
        id=interpreter_id(setup.project_slug, setup.tracker_key),
        task_queue="test",
    )
    await wait_until_state(ticket, "status", lambda s: s["state"] == "awaiting_spec_approval")
    await ticket.signal("human_decision", {"approved": True, "decided_by": "augustin", "kind": "approval"})
    await wait_until_state(ticket, "status", lambda s: s["state"] == "pr_open")
    pr = next(iter(setup.adapters.scm.prs.values())).ref
    setup.adapters.scm.set_checks(pr, "success")
    setup.adapters.scm.submit_review(pr, "marie", "approved")
    for number, (type_, payload) in enumerate(
        (
            ("scm.check.completed", {"conclusion": "success"}),
            ("scm.pr.review_submitted", {"state": "approved"}),
        )
    ):
        await ticket.signal(
            "inbound", {"type": type_, "source": "github", "delivery_id": f"d{number}", "payload": payload}
        )
    train_id = f"train-{setup.project_slug}-prod"
    for _ in range(600):
        if fake_signals(train_id):
            break
        await asyncio.sleep(0.1)
    ((name, boarding),) = fake_signals(train_id)
    assert name == "merged"
    return ticket, {k: v for k, v in boarding.items() if k != "start_payload"}


async def set_prod_approval_policy(setup: Fixture, *, approval: bool) -> None:
    """The project policy (preset `solo`) requires approval in prod; these tests remove it to prove
    that the WORKFLOW's own requirement is enough to hold the batch."""
    from choregos_api.db.models import PolicyDef
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        row = (
            await session.execute(
                select(PolicyDef).where(
                    PolicyDef.project_id == setup.project_id, PolicyDef.is_active.is_(True)
                )
            )
        ).scalar_one()
        doc = copy.deepcopy(row.json_doc)
        doc["release_train"]["prod"]["approval"]["required"] = approval
        row.json_doc = doc


async def start_prod_train(env: Any, setup: Fixture) -> Any:
    return await env.client.start_workflow(
        "ReleaseTrain",
        {"project_slug": setup.project_slug, "env": "prod"},
        id=f"train-{setup.project_slug}-prod",
        task_queue="test",
    )
