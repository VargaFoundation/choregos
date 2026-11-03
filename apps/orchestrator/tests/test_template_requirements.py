"""What the development template needs (S21-22): the reason for a refusal reaches the next agent,
the repository receives the labels the template routes, the PR carries the release notes."""

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


async def _until(setup: Fixture, work_item_run: Any, predicate: Any) -> dict[str, Any]:
    for _ in range(600):
        documents, state = await _documents(setup), await work_item_run.query("status")
        if predicate(documents, state["state"]):
            return documents
        await asyncio.sleep(0.1)
    raise AssertionError(f"never reached: {await _documents(setup)} / {await work_item_run.query('status')}")


async def test_the_reason_for_a_refusal_reaches_the_work_item_and_an_approval_clears_it(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """A transition that declares `inputs: [human_feedback]` receives it; it used to be lost, and the
    agent redid the same work without knowing why it was sent back."""
    for resume in ("spec v1", "spec v2"):
        # `t-refine` declares its outputs: a step without them fails (S31-02).
        setup.adapters.executor.queue_result(
            stage_result(resume, outputs={"spec_markdown": resume, "size": "M", "risk": "low"})
        )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            work_item_run = await temporal_env.client.start_workflow(
                "WorkflowInterpreter",
                {
                    "project_id": setup.project_id,
                    "project_slug": setup.project_slug,
                    "work_item_id": setup.work_item_id,
                    "tracker_key": setup.tracker_key,
                },
                id="wi-billing-api-refusal",
                task_queue="test",
            )
            await _until(setup, work_item_run, lambda d, state: state == "awaiting_spec_approval")
            refusal = {
                "approved": False,
                "decided_by": "augustin",
                "kind": "approval",
                "reason": "scope too broad",
            }
            await work_item_run.signal("human_decision", refusal)
            documents = await _until(
                setup, work_item_run, lambda d, state: d["__runs"] == 2 and state == "awaiting_spec_approval"
            )
            assert documents["human_feedback"] == "scope too broad"

            await work_item_run.signal(
                "human_decision", {"approved": True, "decided_by": "augustin", "kind": "approval"}
            )
            await _until(setup, work_item_run, lambda d, state: "human_feedback" not in d)
            await work_item_run.cancel()


async def test_the_labels_step_adds_the_templates_without_touching_the_platforms() -> None:
    from choregos_orchestrator.activities.provisioning import LABELS, _github_ensure_labels, step_labels

    assert step_labels({}) == LABELS
    from_list = step_labels({"labels": ["bug", "adr"]})
    assert from_list["bug"] == from_list["adr"] == "ededed"
    from_colours = step_labels({"labels": {"complex": "#5319e7", "hotfix": "000000"}})
    assert from_colours["complex"] == "5319e7"
    assert from_colours["hotfix"] == LABELS["hotfix"], "a platform colour is not replaced"

    received: list[dict[str, str]] = []

    async def ensure_labels(labels_given: dict[str, str]) -> None:
        received.append(labels_given)

    bundle = SimpleNamespace(adapters=SimpleNamespace(tracker=SimpleNamespace(ensure_labels=ensure_labels)))
    message = await _github_ensure_labels({"labels": ["study"]}, bundle, None)
    assert received == [{**LABELS, "study": "ededed"}]
    assert message == f"{len(LABELS) + 1} labels ensured"


def test_the_pr_carries_the_release_notes_in_english() -> None:
    from choregos_orchestrator.activities.scm import _pr_body

    item = SimpleNamespace(tracker_key="varga/billing-api#7", body_snapshot="The total ignores credits.")
    body = _pr_body(item, {"release_notes_markdown": "- Credits are now deducted from the total."})
    assert "## Release notes\n- Credits are now deducted from the total." in body
    assert body.startswith("Work item: varga/billing-api#7")
    assert "Ticket :" not in body and "ouverte" not in body  # lang: fr-ok
    assert "## Release notes" not in _pr_body(item, {}), "no empty section"
