"""No pull request is asked for without a branch: the work item escalates instead (S22-46, #539).

On the dev tenant (2026-10-15, v0.17.15), `DiametralGroup/choregos-sandbox-dev#21` (`dev-simple`):
the bug was already fixed on `main`, the implementation changed nothing and pushed no branch. Since
S22-40 the empty diff passes the gates, the work item reached `reviewed`, and `t-open-pr`
(`does: open_pr`) asked GitHub for a pull request from that absent branch: 422 `head invalid`,
retried, and the interpreter died. `open_pull_request` now refuses first, typed and non-retryable,
and the work item escalates with a reason a person can read. Whether such a work item should close
as "already fixed" is #539's product question, not decided here.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from typing import Any

import pytest

from .conftest import Fixture, refine_result, stage_result
from .test_interpreter import _wait_state, _wait_until, scripted, start
from .test_reprise_apres_escalade import _demandes
from .test_restarted_interpreter_resumes import _state_changes

pytestmark = pytest.mark.asyncio

HISTORIES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"
REPO = "varga/billing-api"
BRANCH = "choregos/123"
NO_BRANCH = "no branch: the implementation changed nothing"


def _pr_calls(setup: Fixture, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Every pull request asked of the SCM, by head branch."""
    calls: list[str] = []
    real = setup.adapters.scm.open_pr

    async def open_pr(repo: str, head: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(head)
        return await real(repo, head, *args, **kwargs)

    monkeypatch.setattr(setup.adapters.scm, "open_pr", open_pr)
    return calls


async def _to_the_pull_request(handle: Any, setup: Fixture) -> None:
    """Refine, the spec approved, implement, verify: the work item reaches `t-open-pr`."""
    await _wait_state(handle, "awaiting_spec_approval")
    await handle.signal("human_decision", {"approved": True, "decided_by": "augustin", "kind": "approval"})


def _no_change(setup: Fixture) -> None:
    scripted(
        setup.adapters,
        refine_result("spec"),
        stage_result("Already fixed on main: nothing to change", artifacts={"commits": []}),
        stage_result("verified"),
    )
    setup.adapters.scm.delete_branch(REPO, BRANCH)


async def test_a_no_change_work_item_escalates_without_asking_for_a_pull_request(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    calls = _pr_calls(setup, monkeypatch)
    _no_change(setup)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _to_the_pull_request(handle, setup)
        await _wait_state(handle, "needs_human")
        await _wait_until(handle, "the work item waits for a person", lambda s: s["pending_request"])

        assert calls == [], "no pull request is asked for without a branch"
        assert setup.adapters.scm.prs == {}
        description = await handle.describe()
        assert description.status is not None and description.status.name == "RUNNING"

        # The reason is where a person looks: the request, and the state change.
        asked = [d for d in await _demandes(setup) if (d.payload or {}).get("reason") == "no_branch"]
        assert asked and NO_BRANCH in asked[0].payload["question"], asked
        arrival = [e.payload for e in await _state_changes(setup) if e.payload.get("to") == "needs_human"][-1]
        assert NO_BRANCH in arrival["reason"], arrival
        assert arrival["from"] == "verifying", arrival

        await handle.signal("control", {"action": "stop"})
        await handle.result()
        history = await handle.fetch_history()

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(history)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        target = HISTORIES / "wi-open-pr-without-branch-S22-46.json"
        target.write_text(json.dumps(json.loads(history.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_a_work_item_with_a_branch_still_opens_its_pull_request(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _pr_calls(setup, monkeypatch)
    scripted(
        setup.adapters,
        refine_result("spec"),
        stage_result("implemented", artifacts={"branch": BRANCH, "commits": ["fix(orders): credits"]}),
        stage_result("verified"),
    )
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _to_the_pull_request(handle, setup)
        await _wait_state(handle, "pr_open")
        assert calls == [BRANCH]
        assert [pr.ref.head for pr in setup.adapters.scm.prs.values()] == [BRANCH]
        await handle.signal("control", {"action": "stop"})
        await handle.result()


async def test_an_scm_outage_on_the_branch_is_retried_then_fails_as_before(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only "no branch" escalates: a 5xx reading the branch is retried, then fails the interpreter."""
    from choregos_adapters.errors import UpstreamError
    from temporalio.client import WorkflowFailureError

    calls = _pr_calls(setup, monkeypatch)
    reads: list[str] = []

    async def branch_exists(repo: str, name: str) -> bool:
        reads.append(name)
        raise UpstreamError("github", f"GET ref {name}", status_code=502)

    monkeypatch.setattr(setup.adapters.scm, "branch_exists", branch_exists)
    scripted(setup.adapters, refine_result("spec"), stage_result("implemented"), stage_result("verified"))
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _to_the_pull_request(handle, setup)
        with pytest.raises(WorkflowFailureError):
            # Bounded: without the refusal the work item would wait in `pr_open`, forever.
            await asyncio.wait_for(handle.result(), timeout=120)
    assert len(reads) > 1, "a genuine SCM error is retried"
    assert calls == []


async def test_a_history_before_the_marker_still_fails(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A run recorded before the marker answers no to `patched`: the refusal fails it, as then."""
    from choregos_orchestrator.workflows import interpreter
    from temporalio.client import WorkflowFailureError

    real = interpreter.workflow.patched
    monkeypatch.setattr(
        interpreter.workflow,
        "patched",
        lambda patch_id: (
            False if patch_id == interpreter.OPEN_PR_WITHOUT_BRANCH_ESCALATES else real(patch_id)
        ),
    )
    _no_change(setup)
    async with worker_factory():
        handle = await start(temporal_env, setup)
        await _to_the_pull_request(handle, setup)
        with pytest.raises(WorkflowFailureError):
            # Bounded: without the refusal the work item would wait in `pr_open`, forever.
            await asyncio.wait_for(handle.result(), timeout=120)
