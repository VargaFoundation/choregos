"""The shipped workflows hold their diff guarantees to the merge (S65-07, ADR 0152).

The merge names the commit its own transition's gates judged. A commit that landed after an earlier
step's gates had ruled, and before the merge transition was entered, was therefore judged by the merge
transition's gates only — `ci_green` and the like in the shipped workflows, none of which reads the
diff. Each shipped workflow that merges now declares its diff guarantees on its merge transition.

Three things are played here, on `FakeScm`, whose branches have commits:

- what each diff guarantee judges at a merge transition, where the platform acts and the run the
  interpreter passes is the last step's, or none after a restart;
- why `scope_respected` is not declared there;
- every shipped workflow that merges, walked by a real interpreter: a commit carrying a secret lands
  after the last step that wrote was judged and before the merge transition — nothing is merged, and
  the work item goes where the template's `on_fail` says; with nothing landing, it merges.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from .conftest import Fixture
from .kits.shipped_workflows import (
    ADR,
    ALLOWED,
    BRANCH,
    CODE,
    RECORD,
    REPOSITORY,
    STRAY,
    Shipped,
    approve_the_pull_request,
    journey,
    rests_in,
    shipped_workflows_that_merge,
    verdicts,
    walked,
    work_item,
)
from .test_merged_is_what_was_judged import _ids, _judged, _open_pull_request, _run

pytestmark = pytest.mark.asyncio

SHIPPED = shipped_workflows_that_merge()
BY_LABEL = {shipped.label: shipped for shipped in SHIPPED}

#: The decision record's parameters, as the two shipped workflows that write one declare them.
RECORD_SECTIONS = {
    "paths": ["docs/adr/**"],
    "sections": ["Context and Problem Statement", "Considered Options", "Decision Outcome"],
    "patterns": ['Chosen option: "', 'status: "?accepted'],
}
PROPOSED = [("docs/adr/0001-credits.md", 17, 0, ADR.replace('status: "accepted"', 'status: "proposed"'))]
WITHOUT_ITS_DOCUMENTATION = [file for file in CODE if file[0].startswith(("src/", "tests/"))]
TOO_LARGE = [*CODE, ("src/orders/generated.py", 400, 0, "@@ -0,0 +1,400 @@\n+ROWS = []")]

#: (guarantee, its parameters, a branch that honours it, a branch that breaks it, what the refusal says)
GUARANTEES = [
    pytest.param("no_secrets", {}, CODE, [*CODE, STRAY], "secrets detected: AWS key", id="no_secrets"),
    pytest.param(
        "diff_size_max",
        {"files": 10, "lines": 300},
        CODE,
        TOO_LARGE,
        "lines changed > 300",
        id="diff_size_max",
    ),
    pytest.param(
        "docs_updated",
        {},
        CODE,
        WITHOUT_ITS_DOCUMENTATION,
        "0 documentation file(s) updated",
        id="docs_updated",
    ),
    pytest.param("markdown_sections", RECORD_SECTIONS, RECORD, PROPOSED, "not found", id="markdown_sections"),
]


async def _allow(setup: Fixture, paths: list[str], *, run_id: str = "") -> None:
    """The work item's allowed paths, and those of `run_id`: a run is given its work item's."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.allowed_paths = paths
        if run_id:
            run = await session.get(Run, run_id)
            assert run is not None
            run.allowed_paths = paths


# ───────────────────────── what a diff guarantee judges at a merge transition ─────────────────────────


@pytest.mark.parametrize("last_run", [True, False], ids=["the-last-steps-run", "no-run"])
@pytest.mark.parametrize(("name", "params", "honoured", "broken", "says"), GUARANTEES)
async def test_a_diff_guarantee_at_a_merge_transition_judges_the_commit_that_would_be_merged(
    setup: Fixture,
    name: str,
    params: dict[str, Any],
    honoured: list[Any],
    broken: list[Any],
    says: str,
    last_run: bool,
) -> None:
    """The platform acts at a merge transition: the run the interpreter passes is the last step's, or
    none once it was started again. These guarantees read the work item's whole diff at the commit the
    evaluation judges, so the verdict is the same either way — and it is about the head as it stands,
    not about the commit an earlier step judged."""
    from choregos_orchestrator.activities.gates import evaluate_gates

    scm = setup.adapters.scm
    scm.set_diff(REPOSITORY, "main", BRANCH, honoured)
    run_id = await _run(setup, commits=["c0ffee"]) if last_run else ""
    await _open_pull_request(setup)
    gates = [{"name": "ci_green"}, {"name": name, "params": params}]

    judged = await scm.head_of(REPOSITORY, BRANCH)
    assert all(v["passed"] for v in await evaluate_gates(_ids(setup, run_id=run_id, gates=gates)))
    assert (await _judged(setup))["sha"] == judged

    landed = scm.push(REPOSITORY, BRANCH, broken)
    green, verdict = await evaluate_gates(_ids(setup, run_id=run_id, gates=gates))
    assert green["passed"] is True, "the pull request's checks are the new head's"
    assert verdict["passed"] is False and verdict["pending"] is False, verdict
    assert says in verdict["detail"], verdict
    assert await _judged(setup) == {
        "sha": landed,
        "repository": REPOSITORY,
        "branch": BRANCH,
        "state": "pr_open",
    }, "the refusal is about the commit that landed"


async def test_without_a_run_a_branch_that_is_not_there_is_not_an_empty_diff(setup: Fixture) -> None:
    """A branch nobody pushed is an empty diff only for the run that measured it changed nothing. A
    merge transition judged without a run has no such measure: the guarantees refuse."""
    from choregos_orchestrator.activities.gates import evaluate_gates

    setup.adapters.scm.delete_branch(REPOSITORY, BRANCH)
    gates = [{"name": name} for name in ("no_secrets", "diff_size_max", "docs_updated")]
    refused = await evaluate_gates(_ids(setup, run_id="", gates=gates))
    assert all(not v["passed"] and "diff unavailable" in v["detail"] for v in refused), refused
    assert await _judged(setup) is None, "nothing a merge could name"


# ───────────────────────── why `scope_respected` is not declared there ─────────────────────────


async def test_scope_respected_at_a_merge_transition_is_about_the_last_steps_writes(setup: Fixture) -> None:
    """`scope_respected` judges what a run wrote since the commit where it began (S22-11). At a merge
    transition that run is the last step's: a file that landed before it began is not among its writes,
    and the same branch is refused only when no run is passed. A verdict that depends on which step ran
    last is not a guarantee on what is merged, so the shipped merge transitions do not declare it
    (`RUN_SCOPED_GATES`); this test changes the day the guarantee reads the work item's whole diff there."""
    from choregos_orchestrator.activities.gates import evaluate_gates

    scm = setup.adapters.scm
    outside = [*CODE, (".github/workflows/release.yml", 12, 0)]
    began_at = scm.push(REPOSITORY, BRANCH, outside)  # the branch as the last step found it
    scm.set_diff(REPOSITORY, began_at, BRANCH, [])  # that step wrote nothing
    run_id = await _run(setup, commits=[], start_commit=began_at)
    await _allow(setup, ALLOWED, run_id=run_id)
    await _open_pull_request(setup)
    scope = [{"name": "scope_respected"}]

    [on_the_run] = await evaluate_gates(_ids(setup, run_id=run_id, gates=scope))
    assert (on_the_run["passed"], on_the_run["detail"]) == (True, "within the allowed paths")
    [on_the_work_item] = await evaluate_gates(_ids(setup, run_id="", gates=scope))
    assert on_the_work_item["passed"] is False
    assert on_the_work_item["annotations"] == [".github/workflows/release.yml"]


# ───────────────────────── every shipped workflow that merges, walked ─────────────────────────


def _by_label(*labels: str) -> list[Any]:
    return [pytest.param(BY_LABEL[label], id=label) for label in labels]


EVERY_WORKFLOW = _by_label(*sorted(BY_LABEL))


async def _refusals_of_the_merge(setup: Fixture, shipped: Shipped) -> list[tuple[str, str]]:
    """`(state it went to, reason)` each time the work item left its merge state without merging."""
    return [
        (to, reason)
        for origin, to, reason in await journey(setup)
        if origin == shipped.merge.from_ and to != shipped.merge.to
    ]


@pytest.mark.parametrize("shipped", EVERY_WORKFLOW)
async def test_a_shipped_workflow_still_merges_what_its_gates_judged(
    setup: Fixture, temporal_env: Any, shipped: Shipped
) -> None:
    scm = setup.adapters.scm
    async with walked(setup, temporal_env, shipped):
        item = await rests_in(setup, shipped.merge.to)

    head = await scm.head_of(REPOSITORY, BRANCH)
    assert list(scm.merged_at.values()) == [head] and len(scm.merge_queue) == 1
    assert item.documents["judged_commit"]["sha"] == head
    assert item.documents["judged_commit"]["state"] == shipped.merge.from_
    last = (await verdicts(setup, "no_secrets"))[-1]
    assert last["passed"] is True and last["head_sha"] == head, "the commit merged is one read for secrets"
    assert await _refusals_of_the_merge(setup, shipped) == []


@pytest.mark.parametrize("shipped", EVERY_WORKFLOW)
async def test_a_secret_that_lands_before_the_merge_transition_is_not_merged(
    setup: Fixture, temporal_env: Any, shipped: Shipped
) -> None:
    """The gap this story closes. The steps that write were judged on a clean branch; a commit carrying
    a secret lands as the pull request is opened, green and approved. The merge transition reads it,
    refuses, and the work item follows the template's `on_fail` to a person."""
    scm = setup.adapters.scm
    retry = shipped.merge.on_fail
    async with walked(setup, temporal_env, shipped, lands=[*shipped.branch, STRAY]) as walk:
        await rests_in(setup, retry.escalate_to, never=shipped.merge.to)

    assert scm.merge_queue == [] and scm.merged_at == {}, "nothing was merged"
    walked_through = await journey(setup)
    assert shipped.merge.to not in [to for _, to, _ in walked_through]
    refusals = await _refusals_of_the_merge(setup, shipped)
    assert refusals, walked_through
    assert refusals[0][0] == (retry.to if retry.max_attempts > 1 else retry.escalate_to), refusals
    assert all(reason == "failing gates: no_secrets" for _, reason in refusals), refusals
    [stray] = walk.landed
    refused = [v for v in await verdicts(setup, "no_secrets") if not v["passed"]]
    assert refused and {v["head_sha"] for v in refused} == {stray}, "the verdict names the commit that landed"
    assert refused[0]["annotations"] == ["AWS key"]


@pytest.mark.parametrize(
    ("shipped", "lands", "gate"),
    [
        pytest.param(BY_LABEL["core/hotfix"], TOO_LARGE, "diff_size_max", id="core/hotfix-too-large"),
        pytest.param(
            BY_LABEL["github-software-delivery/dev-complex"],
            WITHOUT_ITS_DOCUMENTATION,
            "docs_updated",
            id="github-software-delivery/dev-complex-documentation-gone",
        ),
        pytest.param(
            BY_LABEL["github-software-delivery/study"],
            PROPOSED,
            "markdown_sections",
            id="github-software-delivery/study-no-longer-accepted",
        ),
    ],
)
async def test_a_commit_that_breaks_another_diff_guarantee_is_not_merged(
    setup: Fixture, temporal_env: Any, shipped: Shipped, lands: list[Any], gate: str
) -> None:
    """The other guarantees a workflow asks of the diff are held the same way: its size, its
    documentation, the decision record's sections and status."""
    scm = setup.adapters.scm
    async with walked(setup, temporal_env, shipped, lands=lands):
        await rests_in(setup, shipped.merge.on_fail.escalate_to, never=shipped.merge.to)

    assert scm.merge_queue == [] and scm.merged_at == {}, "nothing was merged"
    refusals = await _refusals_of_the_merge(setup, shipped)
    assert refusals and refusals[0] == (shipped.merge.on_fail.to, f"failing gates: {gate}"), refusals


async def test_a_secret_that_lands_while_the_merge_waits_for_its_checks_is_not_merged(
    setup: Fixture, temporal_env: Any
) -> None:
    """The merge transition was entered on a clean branch and waits for its checks; the commit lands
    then. Once the checks answer, every guarantee is read again on the head as it stands."""
    shipped = BY_LABEL["core/default-simple"]
    scm = setup.adapters.scm
    async with walked(setup, temporal_env, shipped, green=False) as walk:
        await rests_in(setup, shipped.merge.from_)
        for _ in range(600):  # the merge transition has ruled once, on the clean branch
            if [v for v in await verdicts(setup, "no_secrets") if v["passed"]][1:]:
                break
            await asyncio.sleep(0.1)
        stray = scm.push(REPOSITORY, BRANCH, [*shipped.branch, STRAY])
        approve_the_pull_request(setup)
        for number, (kind, payload) in enumerate(
            (
                ("scm.check.completed", {"conclusion": "success"}),
                ("scm.pr.review_submitted", {"state": "approved"}),
            )
        ):
            event = {"type": kind, "source": "github", "delivery_id": f"d{number}", "payload": payload}
            await walk.handle.signal("inbound", event)
        await rests_in(setup, shipped.merge.on_fail.escalate_to, never=shipped.merge.to)

    assert scm.merge_queue == [] and scm.merged_at == {}, "nothing was merged"
    refused = [v for v in await verdicts(setup, "no_secrets") if not v["passed"]]
    assert refused and {v["head_sha"] for v in refused} == {stray}
    assert (await work_item(setup)).state == "needs_human"
