# SPDX-License-Identifier: Apache-2.0
"""The interpreter takes the transition that declares it repairs an outcome (ADR 0181 C1).

A workflow's merge waits for the CI; the CI fails; the work item goes back to `mending`, where two
agents may act: `t-redo` (the normal path, declared first) and `t-mend`, which declares
`repairs: ci_failed` and whose role, `mend`, is no role the platform ever guessed a repair from. The
real interpreter, on Temporal's test server, plays `t-mend` on the failure, then merges once the CI
passes. The history replays, holds the `repairs-declared` marker, and is archived for `tests/replay`
(`TRANIBOR_RECORD_HISTORY=1`). Without the marker (a history recorded before it), the interpreter
does not read `repairs:` and the same workflow would take `t-redo`.
"""

from __future__ import annotations

import asyncio
import base64
import json
import pathlib
from typing import Any

import pytest

from .conftest import Fixture, recording_history, stage_result

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"

MENDING = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: mending-desk, version: 1 }
initial: ready
actors:
  writer: { type: agent, role: implement, model: "profile:standard", needs: [scm] }
  mender: { type: agent, role: mend, playbook: implement, model: "profile:standard", needs: [scm] }
  ci: { type: system }
states:
  ready: { display: Ready }
  in_progress: { display: In progress }
  pr_open: { display: PR open, kind: wait }
  mending: { display: Mending }
  merged: { display: Merged, terminal: true }
  needs_human: { display: Needs a human, kind: wait }
transitions:
  - { id: t-write, from: ready, to: in_progress, by: writer }
  - { id: t-open, from: in_progress, to: pr_open, by: ci, does: open_pr }
  - id: t-merge
    from: pr_open
    to: merged
    by: ci
    gates: [ci_green]
    timeout_hours: 48
    on_fail: { to: mending, max_attempts: 3, escalate_to: needs_human }
    does: merge_pr
  - id: t-redo
    from: mending
    to: pr_open
    by: writer
    on_fail: { to: mending, max_attempts: 2, escalate_to: needs_human }
  - id: t-mend
    from: mending
    to: pr_open
    by: mender
    repairs: ci_failed
    on_fail: { to: mending, max_attempts: 2, escalate_to: needs_human }
"""


async def _start(setup: Fixture, temporal_env: Any) -> Any:
    from tranibor_api.db.models import Project, WorkItem
    from tranibor_api.db.session import session_scope
    from tranibor_api.rbac import SYSTEM
    from tranibor_api.services.definitions import publish_workflow
    from tranibor_api.temporal import interpreter_id

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        definition = await publish_workflow(session, SYSTEM, project, MENDING)
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id = definition.id
        item.state = "ready"
    return await temporal_env.client.start_workflow(
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


async def _until(predicate: Any, what: str, timeout: float = 60) -> None:
    async def poll() -> None:
        while not predicate():
            await asyncio.sleep(0.05)

    try:
        await asyncio.wait_for(poll(), timeout=timeout)
    except TimeoutError:
        raise AssertionError(f"timed out waiting for {what}") from None


async def _state(handle: Any, state: str) -> None:
    for _ in range(600):
        if (await handle.query("status"))["state"] == state:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"the work item never reached `{state}`")


def _patches(history: dict[str, Any]) -> list[str]:
    """The patch ids a history's markers record (decoded from their payloads)."""
    found: list[str] = []
    for event in history["events"]:
        attributes = event.get("markerRecordedEventAttributes") or {}
        for payloads in (attributes.get("details") or {}).values():
            for payload in payloads.get("payloads", []):
                found.append(base64.b64decode(payload.get("data", "")).decode("utf-8", "replace"))
    return found


async def test_the_interpreter_takes_the_declared_repair_and_the_history_replays(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    from temporalio.worker import Replayer
    from tranibor_orchestrator.workflows import ALL_WORKFLOWS
    from tranibor_orchestrator.workflows.interpreter import REPAIRS_DECLARED

    played: list[str] = []

    scm = setup.adapters.scm

    def handler(stage_input: Any) -> Any:
        played.append(stage_input.transition.id)
        if scm.prs:  # a repair, whichever: the CI passes again on its push
            scm.set_checks(next(iter(scm.prs.values())).ref, "success")
        return stage_result(f"{stage_input.transition.id} done")

    setup.adapters.executor.handler = handler
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            handle = await _start(setup, temporal_env)
            await _state(handle, "pr_open")
            await _until(lambda: bool(scm.prs), "the pull request")
            ref = next(iter(scm.prs.values())).ref
            # The CI's failure arrives as an event while the merge waits for it: the interpreter reads
            # the outcome from the event (`ci_failed`), then the check it names fails the gate. The
            # pause lets the merge's first evaluation (pending, no check yet) happen first.
            await asyncio.sleep(1)
            failed = {"conclusion": "failure"}
            await handle.signal(
                "inbound",
                {"type": "ci.run.failed", "source": "github", "delivery_id": "d1", "payload": failed},
            )
            await asyncio.sleep(1)
            scm.set_checks(ref, "failure")
            await handle.signal(
                "inbound",
                {"type": "scm.check.completed", "source": "github", "delivery_id": "d2", "payload": failed},
            )
            outcome = await asyncio.wait_for(handle.result(), timeout=60)

    assert outcome["state"] == "merged"
    assert played == ["t-write", "t-mend"], "the failure was repaired by `t-mend`, not the normal `t-redo`"
    history = await handle.fetch_history()
    assert any(REPAIRS_DECLARED in patch for patch in _patches(json.loads(history.to_json()))), (
        "the history records the `repairs-declared` marker"
    )
    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if recording_history():
        target = HISTORIES / "wi-repairs-declared-S83-03c.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")
