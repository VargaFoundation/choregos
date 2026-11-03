"""The core templates walked through where they used to break (S21-26).

`full-auto` and `advanced` carried their CI fixer from `pr_open`, where it was chosen only on a
result the interpreter had already forgotten: a red CI ended with a human (#284). The release notes
of `advanced` were a `merged → merged` loop never chosen (#285), and `provenance_signed` waited
forever (#283).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

import pytest

from .conftest import Fixture, stage_result

pytestmark = pytest.mark.asyncio


async def _pin(setup: Fixture, name: str) -> None:
    """Publish the core template `name` in the project and pin the fixture work item to it."""
    from choregos_api.db.models import Project, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.rbac import SYSTEM
    from choregos_api.services.definitions import publier_workflow
    from choregos_core.dsl import template_yaml

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        definition = await publier_workflow(session, SYSTEM, project, template_yaml(name))
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id = definition.id


def _agents(results: dict[str, Callable[[], Any]]) -> Callable[[Any], Any]:
    def play(stage_input: Any) -> Any:
        factory = results.get(stage_input.transition.id)
        assert factory is not None, f"no scripted result for {stage_input.transition.id}"
        return factory()

    return play


async def _start(env: Any, setup: Fixture) -> Any:
    return await env.client.start_workflow(
        "WorkflowInterpreter",
        {
            "project_id": setup.project_id,
            "project_slug": setup.project_slug,
            "work_item_id": setup.work_item_id,
            "tracker_key": setup.tracker_key,
        },
        id="wi-billing-api-template",
        task_queue="test",
    )


async def _state(work_item_run: Any, state: str, timeout: float = 60.0) -> None:
    for _ in range(int(timeout * 10)):
        if (await work_item_run.query("status"))["state"] == state:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"never in `{state}`: {await work_item_run.query('status')}")


async def _ci(setup: Fixture, work_item_run: Any, conclusion: str, delivery: str) -> None:
    for _ in range(600):
        if setup.adapters.scm.prs:
            break
        await asyncio.sleep(0.1)
    pr = next(iter(setup.adapters.scm.prs.values())).ref
    setup.adapters.scm.set_checks(pr, conclusion)
    payload = {"conclusion": conclusion}
    event = {
        "type": "scm.check.completed",
        "source": "github",
        "delivery_id": delivery,
        "payload": payload,
    }
    await work_item_run.signal("inbound", event)


async def _played_transitions(setup: Fixture) -> list[str]:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = (
            await session.execute(
                select(Run).where(Run.work_item_id == setup.work_item_id).order_by(Run.created_at)
            )
        ).scalars()
        return [str(r.transition_id) for r in rows]


def _push(setup: Fixture) -> Any:
    """The fixer pushes: as on GitHub, the checks of the new commit go back to pending."""
    for pr in setup.adapters.scm.prs.values():
        pr.checks = []
    return stage_result("flaky import fixed", artifacts={"branch": "choregos/123"})


def _outputs(**outputs: Any) -> Callable[[], Any]:
    return lambda: stage_result("done", outputs=outputs)


async def test_full_auto_a_red_ci_goes_through_the_fixer_then_merges(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    await _pin(setup, "full-auto")
    setup.adapters.executor.handler = _agents(
        {
            "t-triage": _outputs(size="M", risk="low"),
            "t-refine": _outputs(spec_markdown="## Spec", allowed_paths=["src/orders/**", "tests/orders/**"]),
            "t-implement": lambda: stage_result("done", artifacts={"branch": "choregos/123"}),
            "t-verify": _outputs(),
            "t-review": _outputs(review_markdown="Fine.", verdict="approve"),
            "t-fix-ci": lambda: _push(setup),
        }
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            work_item_run = await _start(temporal_env, setup)
            await _state(work_item_run, "pr_open")
            await _ci(setup, work_item_run, "failure", "ci-red")
            for _ in range(600):
                if "t-fix-ci" in await _played_transitions(setup):
                    break
                await asyncio.sleep(0.1)
            await _state(work_item_run, "pr_open")
            await _ci(setup, work_item_run, "success", "ci-green")
            await _state(work_item_run, "merged")
            await work_item_run.cancel()
    played = await _played_transitions(setup)
    assert played.count("t-fix-ci") == 1, played
    assert setup.adapters.scm.merge_queue, "the PR went to merge after the fix"


async def test_advanced_writes_its_release_notes_before_the_pr_and_merges_without_provenance(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    await _pin(setup, "advanced")
    setup.adapters.executor.handler = _agents(
        {
            "t-triage": _outputs(size="M", risk="low"),
            "t-refine": _outputs(spec_markdown="## Spec", allowed_paths=["src/orders/**", "tests/orders/**"]),
            "t-plan": _outputs(plan_markdown="1. credits"),
            "t-implement": lambda: stage_result("done", artifacts={"branch": "choregos/123"}),
            "t-verify": _outputs(),
            "t-agent-review": _outputs(review_markdown="Fine.", verdict="approve"),
            "t-release-notes": _outputs(release_notes_markdown="- Credits are deducted from the total."),
        }
    )
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            work_item_run = await _start(temporal_env, setup)
            await _state(work_item_run, "awaiting_spec_approval")
            await work_item_run.signal(
                "human_decision", {"approved": True, "decided_by": "marie", "kind": "approval"}
            )
            await _state(work_item_run, "pr_open")
            pr = next(iter(setup.adapters.scm.prs.values()))
            assert "## Release notes\n- Credits are deducted from the total." in pr.body
            setup.adapters.scm.submit_review(pr.ref, "marie", "approved")
            await _ci(setup, work_item_run, "success", "ci-green")
            await _state(work_item_run, "merged")
            await work_item_run.cancel()
    played = await _played_transitions(setup)
    assert played.index("t-release-notes") < len(played), played
    assert setup.adapters.scm.merge_queue, "no guard waits for a provenance that nothing supplies"
