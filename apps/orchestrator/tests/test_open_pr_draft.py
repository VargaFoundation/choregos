# SPDX-License-Identifier: Apache-2.0
"""`does: open_pr` can open a draft pull request (S51-11).

Until then `open_pull_request` always opened a pull request ready for review (`draft=False`), and
marked it ready again each time the transition was taken. A transition now says
`pull_request: { draft: true }`: the pull request is born a draft, and one already open on the branch
keeps its state — a person who marked it ready is not overruled. Without it, nothing changes: the
activity's input is the one recorded histories hold, and the pull request is opened ready.
"""

from __future__ import annotations

import json
import pathlib
from typing import Any

import pytest

from .conftest import Fixture, recording_history, refine_result, stage_result
from .test_interpreter import _wait_state, scripted, start

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
OPEN_PR = "    does: open_pr\n"


async def _publish(setup: Fixture, text: str) -> None:
    """The project's active workflow becomes `text`, as a publication would make it."""
    from choregos_api.db.models import WorkflowDef
    from choregos_api.db.session import session_scope
    from choregos_core import checksum, parse_workflow
    from sqlalchemy import select

    wf, report = parse_workflow(text, strict=False)
    assert report.valid, [e.message for e in report.errors]
    async with session_scope() as session:
        definition = (
            await session.execute(
                select(WorkflowDef).where(WorkflowDef.project_id == setup.project_id, WorkflowDef.is_active)
            )
        ).scalar_one()
        definition.yaml = text
        definition.json_doc = wf.model_dump(mode="json", by_alias=True, exclude_none=True)
        definition.checksum = checksum(wf)


def _draft_workflow() -> str:
    from choregos_core.dsl import template_yaml

    text = template_yaml("default-simple")
    assert text.count(OPEN_PR) == 1
    return text.replace(OPEN_PR, OPEN_PR + "    pull_request: { draft: true }\n")


async def _open(setup: Fixture, **extra: Any) -> Any:
    from choregos_orchestrator.activities.scm import open_pull_request

    await open_pull_request({"project_id": setup.project_id, "work_item_id": setup.work_item_id, **extra})
    (pr,) = setup.adapters.scm.prs.values()
    return pr


async def test_without_the_option_the_pull_request_is_ready_for_review(setup: Fixture) -> None:
    pr = await _open(setup)
    assert pr.draft is False


async def test_the_option_opens_a_draft(setup: Fixture) -> None:
    pr = await _open(setup, draft=True)
    assert pr.draft is True


async def test_a_draft_opened_again_keeps_the_state_a_person_gave_it(setup: Fixture) -> None:
    pr = await _open(setup, draft=True)
    await setup.adapters.scm.update_pr(pr.ref, body=None, draft=False)  # marked ready by a person
    again = await _open(setup, draft=True)
    assert again.draft is False, "the transition taken again does not put the pull request back to draft"
    assert len(setup.adapters.scm.prs) == 1


async def test_a_workflow_that_asks_for_a_draft_opens_one(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await _publish(setup, _draft_workflow())
    scripted(
        setup.adapters,
        refine_result("spec"),
        stage_result(
            "implemented", artifacts={"branch": "choregos/123", "commits": ["fix(orders): credits"]}
        ),
        stage_result("verified"),
    )
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal(
            "human_decision", {"approved": True, "decided_by": "augustin", "kind": "approval"}
        )
        await _wait_state(handle, "pr_open")
        (pr,) = setup.adapters.scm.prs.values()
        assert pr.draft is True, "`pull_request: {draft: true}` opened a draft"
        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "wi-open-pr-draft-S51-11.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")
