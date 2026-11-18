"""The workflows the repository ships that merge a pull request, and a way to walk any of them.

Every one is played the same way: the fake executor answers each agent step with what its transition
declares — and with the commit where its run began, as the runner does —, a person approves whatever
waits for one, and the pull request is green and approved as soon as it is opened. What the branch
holds is scripted on `FakeScm`, whose branches have commits.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..conftest import Fixture, stage_result
from .workers import worker_with

ROOT = Path(__file__).resolve().parents[4]
REPOSITORY = "northwind/billing-api"
BRANCH = "choregos/123"
ALLOWED = ["src/**", "tests/**", "docs/**", "CHANGELOG.md"]

#: Built at run time: the pattern `no_secrets` reads, never a literal key in the tree.
SECRET = "AKIA" + "ABCDEFGHIJKLMNOP"

ADR = """+---
+status: "accepted"
+---
+# Deduct credits from the total
+
+## Context and Problem Statement
+
+An order with a credit shows a total that ignores it (src/orders/total.py:42).
+
+## Considered Options
+
+* Deduct at display
+* Deduct at computation
+
+## Decision Outcome
+
+Chosen option: "Deduct at computation", because every reader of the total then agrees.
"""

#: What the branch of a change to the code holds: code, its tests, its documentation.
CODE = [
    ("src/orders/total.py", 20, 4, "@@ -40,4 +40,20 @@\n+total -= credits"),
    ("tests/orders/test_total.py", 30, 0, "@@ -0,0 +1,30 @@\n+def test_credits_are_deducted(): ..."),
    ("docs/orders/total.md", 6, 0, "@@ -0,0 +1,6 @@\n+Credits are deducted from the total."),
    ("CHANGELOG.md", 1, 0, "@@ -3,0 +4 @@\n+- Credits are deducted from the total."),
]
#: What the branch of a decision record holds: the record, accepted, and nothing else.
RECORD = [("docs/adr/0001-credits.md", 17, 0, ADR)]
#: The file of a commit nobody judged: inside the allowed paths, and carrying a secret.
STRAY = ("src/orders/settings.py", 1, 0, f'@@ -0,0 +1 @@\n+ACCESS_KEY = "{SECRET}"')
#: The file of another: under a path the fixture project's policy forbids (the `solo` preset's
#: `scope.deny_paths`), whatever the allowed paths say.
DENIED = (".github/workflows/release.yml", 12, 0, "@@ -0,0 +1,12 @@\n+on: push")

#: What an agent answers for the outputs the gates read; any other declared output gets a sentence.
ANSWERS: dict[str, Any] = {"size": "M", "risk": "low", "verdict": "approve", "allowed_paths": ALLOWED}


@dataclass(frozen=True)
class Shipped:
    """One shipped workflow that merges: where it comes from, its text, its merge transition."""

    source: str
    name: str
    yaml: str
    workflow: Any
    merge: Any

    @property
    def label(self) -> str:
        return f"{self.source}/{self.name}"

    @property
    def writes_a_record(self) -> bool:
        """A workflow whose steps are asked for a decision record (`markdown_sections`), not code."""
        return any(g.name == "markdown_sections" for t in self.workflow.transitions for g in t.gates)

    @property
    def escalations(self) -> set[str]:
        """The states a failing transition hands the work item to a person in."""
        return {
            t.on_fail.escalate_to for t in self.workflow.transitions if t.on_fail and t.on_fail.escalate_to
        }

    @property
    def branch(self) -> list[tuple[Any, ...]]:
        return list(RECORD if self.writes_a_record else CODE)

    @property
    def opening(self) -> Any:
        """The transition that opens the pull request."""
        from tranibor_contracts.workflow import transition_effect

        return next(t for t in self.workflow.transitions if transition_effect(t) == "open_pr")

    @property
    def scoped_steps(self) -> list[Any]:
        """The steps whose scope is judged on their own run's writes, in the order they are written."""
        from tranibor_core.gates import judged_on_a_run

        return [
            transition
            for transition in self.workflow.transitions
            if any(judged_on_a_run(gate.name, gate.params) for gate in transition.gates)
        ]

    @property
    def scope_guard(self) -> Any:
        """The first transition, from the opening of the pull request on, that reads the scope over the
        work item's whole diff: the merge, or the opening itself where the workflow asks it there."""
        from tranibor_core.gates import OVER_WORK_ITEM

        def whole(transition: Any) -> bool:
            return any(
                g.name == "scope_respected" and g.params.get("over") == OVER_WORK_ITEM
                for g in transition.gates
            )

        return self.opening if whole(self.opening) else self.merge


def _merge_of(workflow: Any) -> Any | None:
    from tranibor_contracts import SystemActor
    from tranibor_contracts.workflow import transition_effect

    for transition in workflow.transitions:
        if (
            isinstance(workflow.actor_of(transition), SystemActor)
            and transition_effect(transition) == "merge_pr"
        ):
            return transition
    return None


def shipped_workflows_that_merge() -> list[Shipped]:
    """The core's workflows and those of the templates in `templates/`, read from the files shipped."""
    from tranibor_core import parse_workflow
    from tranibor_core.dsl import TEMPLATE_NAMES, template_yaml

    texts = [("core", template_yaml(name)) for name in TEMPLATE_NAMES]
    for path in sorted((ROOT / "templates").glob("*/workflows/*.yaml")):
        texts.append((path.parents[1].name, path.read_text(encoding="utf-8")))
    found = []
    for source, text in texts:
        workflow, _ = parse_workflow(text, strict=False)
        merge = _merge_of(workflow)
        if merge is not None:
            found.append(Shipped(source, workflow.metadata.name, text, workflow, merge))
    return found


async def pin(setup: Fixture, shipped: Shipped) -> None:
    """Publish the workflow in the fixture's project and pin its work item to it."""
    from tranibor_api.db.models import Project, WorkItem
    from tranibor_api.db.session import session_scope
    from tranibor_api.rbac import SYSTEM
    from tranibor_api.services.definitions import publish_workflow

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        definition = await publish_workflow(session, SYSTEM, project, shipped.yaml)
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.workflow_def_id = definition.id
        item.allowed_paths = list(ALLOWED)
    setup.adapters.scm.set_diff(REPOSITORY, "main", BRANCH, shipped.branch)


def answer(stage_input: Any, began_at: str) -> Any:
    """What any agent step answers: each output its transition declares, an approving verdict, and —
    as the runner reports it — the commit where its run began."""
    transition = stage_input.transition
    outputs = {name: ANSWERS.get(name, f"{name} of {transition.id}") for name in transition.outputs}
    result = stage_result(
        f"{transition.id} done", outputs={"verdict": "approve", **outputs}, artifacts={"branch": BRANCH}
    )
    result.artifacts.reports["start_commit"] = began_at
    return result


async def work_item(setup: Fixture) -> Any:
    from tranibor_api.db.models import WorkItem
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        session.expunge(item)
        return item


async def journey(setup: Fixture) -> list[tuple[str, str, str]]:
    """`(from, to, reason)` of each state the work item changed to, in order."""
    from sqlalchemy import select
    from tranibor_api.db.models import Event
    from tranibor_api.db.session import session_scope
    from tranibor_contracts import EventType

    async with session_scope() as session:
        rows = await session.execute(
            select(Event)
            .where(
                Event.type == str(EventType.WORKITEM_STATE_CHANGED),
                Event.work_item_id == setup.work_item_id,
            )
            .order_by(Event.ts)
        )
        return [
            (str(e.payload.get("from")), str(e.payload.get("to")), str(e.payload.get("reason", "")))
            for e in rows.scalars()
        ]


async def verdicts(setup: Fixture, gate: str) -> list[dict[str, Any]]:
    """Every verdict of `gate` journaled on the work item's runs, in order."""
    from sqlalchemy import select
    from tranibor_api.db.models import Run, RunEvent
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        rows = await session.execute(
            select(RunEvent)
            .join(Run, Run.id == RunEvent.run_id)
            .where(Run.work_item_id == setup.work_item_id, RunEvent.type == "gate.outcome")
            .order_by(RunEvent.ts, RunEvent.seq)
        )
        return [dict(e.payload) for e in rows.scalars() if e.payload.get("name") == gate]


async def _approve_whatever_waits(handle: Any, escalations: set[str]) -> None:
    """A person approves each request the work item raises, once, except where a failing transition
    escalated it: the tests wait for it there, and an approval would move it on before they look.

    The decision names the request the person saw (#1795): one delivered after the work item moved on
    to an escalation is no longer for the request pending there, and the interpreter ignores it."""
    answered: set[str] = set()
    while True:
        with contextlib.suppress(Exception):  # a query that times out is asked again
            status = await handle.query("status")
            request = status.get("pending_request")
            if request and request not in answered and status.get("state") not in escalations:
                answered.add(request)
                await handle.signal(
                    "human_decision",
                    {"request_id": request, "approved": True, "decided_by": "marie", "kind": "approval"},
                )
        await asyncio.sleep(0.1)


@dataclass
class Walk:
    """A work item being walked: its interpreter, and the commits that landed on its branch unjudged."""

    handle: Any
    landed: list[str] = field(default_factory=list)


def approve_the_pull_request(setup: Fixture) -> None:
    """The pull request's checks are green and a person approved it: nothing is left to wait for."""
    scm = setup.adapters.scm
    pull_request = next(iter(scm.prs.values()))
    scm.set_checks(pull_request.ref, "success")
    if not any(review.state == "approved" for review in pull_request.reviews):
        scm.submit_review(pull_request.ref, "marie", "approved")


@contextlib.asynccontextmanager
async def walked(
    setup: Fixture,
    temporal_env: Any,
    shipped: Shipped,
    *,
    lands: list[tuple[Any, ...]] | None = None,
    after_step: str | None = None,
    green: bool = True,
) -> AsyncIterator[Walk]:
    """The work item walked through `shipped` by a real interpreter, up to wherever it rests.

    The pull request is green and approved the moment it is opened (`green`). With `lands`, a commit
    nobody judged lands on the branch right then — after the gates of every step that wrote, before
    the merge transition is entered — and the branch holds `lands` from there on. With `after_step`
    it lands earlier: once that step's gates have ruled, before the next agent step begins, so that
    every later run starts from a branch that already holds it."""
    from temporalio import activity
    from tranibor_api.temporal import interpreter_id
    from tranibor_orchestrator.activities.scm import open_pull_request

    scm = setup.adapters.scm
    walk = Walk(handle=None)
    played: list[str] = []

    def land() -> None:
        if lands is not None and not walk.landed:
            walk.landed.append(scm.push(REPOSITORY, BRANCH, lands))

    def any_agent(stage_input: Any) -> Any:
        if after_step is not None and played and played[-1] == after_step:
            land()
        played.append(stage_input.transition.id)
        return answer(stage_input, scm.heads[(REPOSITORY, BRANCH)])

    @activity.defn(name="open_pull_request")
    async def opened_then_a_commit_lands(payload: dict[str, Any]) -> dict[str, Any]:
        opened = await open_pull_request(payload)
        if green:
            approve_the_pull_request(setup)
        if after_step is None:
            land()
        return opened

    await pin(setup, shipped)
    setup.adapters.executor.handler = any_agent
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_with(temporal_env, {"open_pull_request": opened_then_a_commit_lands}):
            walk.handle = await temporal_env.client.start_workflow(
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
            person = asyncio.create_task(_approve_whatever_waits(walk.handle, shipped.escalations))
            try:
                yield walk
            finally:
                person.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await person
                with contextlib.suppress(Exception):
                    await walk.handle.terminate("end of the test")


async def rests_in(setup: Fixture, state: str, *, never: str | None = None, timeout: float = 120.0) -> Any:
    """Wait until the work item is in `state`, read from its row: a state it rests in. Reaching `never`
    on the way ends the wait at once, with where the work item went."""
    for _ in range(int(timeout * 10)):
        item = await work_item(setup)
        if item.state == state:
            return item
        if never is not None and item.state == never:
            raise AssertionError(f"in `{never}`, never in `{state}`: {await journey(setup)}")
        await asyncio.sleep(0.1)
    item = await work_item(setup)
    raise AssertionError(f"never in `{state}`: in `{item.state}` after {await journey(setup)}")
