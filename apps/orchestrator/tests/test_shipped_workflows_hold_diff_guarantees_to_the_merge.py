"""The shipped workflows hold their diff guarantees to the merge (S65-07, ADR 0171).

The merge names the commit its own transition's gates judged. A commit that landed after an earlier
step's gates had ruled, and before the merge transition was entered, was therefore judged by the merge
transition's gates only — `ci_green` and the like in the shipped workflows, none of which reads the
diff. Each shipped workflow that merges now declares its diff guarantees on its merge transition, the
scope among them: `scope_respected` with `over: work_item` reads the work item's whole diff, and
nothing of a run.

Three things are played here, on `FakeScm`, whose branches have commits:

- what each diff guarantee judges at a merge transition, where the platform acts and the run the
  interpreter passes is the last step's, or none after a restart;
- the two readings of `scope_respected`: a run's writes, and the work item's whole diff;
- every shipped workflow that merges, walked by a real interpreter: a commit carrying a secret, or a
  file under a path the policy forbids, lands after the last step that wrote was judged and before the
  merge transition — nothing is merged, and the work item goes where the template's `on_fail` says;
  with nothing landing, it merges.
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
    DENIED,
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
#: A file that is no part of a decision record: outside the paths its workflow declares.
NOT_THE_RECORD = ("src/orders/total.py", 3, 0, "@@ -40,0 +41,3 @@\n+total -= credits")

#: A file outside the allowed paths the kit gives a work item, and under no path the policy forbids.
ELSEWHERE = ("ops/deploy.sh", 5, 0, "@@ -0,0 +1,5 @@\n+set -eu")

OVER_THE_WORK_ITEM = {"over": "work_item"}
OVER_THE_RECORD = {"over": "work_item", "paths": ["docs/adr/**"]}

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
    pytest.param(
        "scope_respected",
        OVER_THE_WORK_ITEM,
        CODE,
        [*CODE, DENIED],
        "under a path the project's policy forbids",
        id="scope_respected-over-the-work-item",
    ),
    pytest.param(
        "scope_respected",
        OVER_THE_RECORD,
        RECORD,
        [*RECORD, NOT_THE_RECORD],
        "1 file(s) outside the declared paths",
        id="scope_respected-over-the-work-item-with-paths",
    ),
]


async def _allow(setup: Fixture, paths: list[str], *, run_id: str = "") -> None:
    """The work item's allowed paths, and those of `run_id`: a run is given its work item's."""
    from tranibor_api.db.models import Run, WorkItem
    from tranibor_api.db.session import session_scope

    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.allowed_paths = paths
        if run_id:
            run = await session.get(Run, run_id)
            assert run is not None
            run.allowed_paths = paths


async def _a_last_step_that_began_after(setup: Fixture, branch: list[Any]) -> str:
    """The work item at its merge transition, its pull request open and green. `branch` landed before
    its last step began: that step's run starts from it, wrote nothing, and has the work item's paths."""
    scm = setup.adapters.scm
    began_at = scm.push(REPOSITORY, BRANCH, branch)
    scm.set_diff(REPOSITORY, began_at, BRANCH, [])
    run_id = await _run(setup, commits=[], start_commit=began_at)
    await _allow(setup, ALLOWED, run_id=run_id)
    await _open_pull_request(setup)
    return run_id


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
    from tranibor_orchestrator.activities.gates import evaluate_gates

    scm = setup.adapters.scm
    scm.set_diff(REPOSITORY, "main", BRANCH, honoured)
    run_id = await _run(setup, commits=["c0ffee"]) if last_run else ""
    await _allow(setup, ALLOWED, run_id=run_id)
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
    from tranibor_orchestrator.activities.gates import evaluate_gates

    setup.adapters.scm.delete_branch(REPOSITORY, BRANCH)
    gates = [{"name": name} for name in ("no_secrets", "diff_size_max", "docs_updated")]
    gates.append({"name": "scope_respected", "params": OVER_THE_WORK_ITEM})
    refused = await evaluate_gates(_ids(setup, run_id="", gates=gates))
    assert all(not v["passed"] and "diff unavailable" in v["detail"] for v in refused), refused
    assert await _judged(setup) is None, "nothing a merge could name"


# ───────────────────────── the two readings of `scope_respected` ─────────────────────────


async def test_without_over_scope_respected_judges_what_a_run_wrote(setup: Fixture) -> None:
    """The reading it had: what a run wrote since the commit where it began (S22-11), against that
    run's paths. At a merge transition the run is the last step's: a file that landed before it began
    is not among its writes, and the same branch is refused only when no run is passed. That is why
    the validator warns about this reading on a transition the platform takes."""
    from tranibor_orchestrator.activities.gates import evaluate_gates

    run_id = await _a_last_step_that_began_after(setup, [*CODE, DENIED])
    on_a_run = [{"name": "scope_respected"}]

    [with_the_run] = await evaluate_gates(_ids(setup, run_id=run_id, gates=on_a_run))
    assert (with_the_run["passed"], with_the_run["detail"]) == (True, "within the allowed paths")
    [without] = await evaluate_gates(_ids(setup, run_id="", gates=on_a_run))
    assert without["passed"] is False and without["annotations"] == [DENIED[0]]


async def test_over_the_work_item_the_verdict_is_the_same_with_the_last_steps_run_and_with_none(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file under a path the policy forbids landed before the last step began. `over: work_item`
    reads the work item's whole diff at the judged commit and nothing of a run — no start commit, no
    run's paths —, so the verdict does not depend on whether the interpreter still knows that step."""
    from tranibor_orchestrator.activities.gates import evaluate_gates

    scm = setup.adapters.scm
    run_id = await _a_last_step_that_began_after(setup, [*CODE, DENIED])
    head = await scm.head_of(REPOSITORY, BRANCH)
    compared: list[tuple[str, str]] = []
    compare = scm.compare

    async def recorded_compare(repo: str, base: str, to: str) -> Any:
        compared.append((base, to))
        return await compare(repo, base, to)

    monkeypatch.setattr(scm, "compare", recorded_compare)
    whole = [{"name": "scope_respected", "params": OVER_THE_WORK_ITEM}]

    [with_the_run] = await evaluate_gates(_ids(setup, run_id=run_id, gates=whole))
    [without] = await evaluate_gates(_ids(setup, run_id="", gates=whole))

    assert with_the_run == without
    assert with_the_run["passed"] is False and with_the_run["annotations"] == [DENIED[0]]
    assert "scope.deny_paths" in with_the_run["detail"]
    assert compared == [("main", head), ("main", head)], "the diff from the default branch, and no run's"


async def test_over_the_work_item_the_work_items_allowed_paths_are_not_read(setup: Fixture) -> None:
    """A work item's allowed paths are rewritten by its steps' results: a specification narrows them
    after an earlier step wrote, legitimately, elsewhere. Read at the merge they would refuse that
    work, so `over: work_item` holds the paths the transition declares, and without any the paths the
    policy forbids."""
    from tranibor_orchestrator.activities.gates import evaluate_gates

    scm = setup.adapters.scm
    scm.set_diff(REPOSITORY, "main", BRANCH, [*RECORD, NOT_THE_RECORD])  # a record, then the code
    await _allow(setup, ["src/**"])  # what a later specification left the work item with
    await _open_pull_request(setup)

    [whole] = await evaluate_gates(
        _ids(setup, gates=[{"name": "scope_respected", "params": OVER_THE_WORK_ITEM}])
    )
    assert whole["passed"] is True and "only the paths the policy forbids are held" in whole["detail"]
    [declared] = await evaluate_gates(
        _ids(setup, gates=[{"name": "scope_respected", "params": {"over": "work_item", "paths": ["src/**"]}}])
    )
    assert declared["passed"] is False and declared["annotations"] == [RECORD[0][0]]
    [on_the_item] = await evaluate_gates(_ids(setup, gates=[{"name": "scope_respected"}]))
    assert on_the_item["passed"] is False, "the reading without `over` does read them, when no run is passed"


async def test_the_scope_check_run_of_a_merge_transition_marks_the_commit_it_read(setup: Fixture) -> None:
    """`choregos/scope` on the pull request says what the merge transition read, on the judged commit."""
    from tranibor_orchestrator.activities.gates import evaluate_gates

    scm = setup.adapters.scm
    await _open_pull_request(setup)
    landed = scm.push(REPOSITORY, BRANCH, [*CODE, DENIED])
    await evaluate_gates(_ids(setup, gates=[{"name": "scope_respected", "params": OVER_THE_WORK_ITEM}]))
    assert [(c["name"], c["sha"], c["conclusion"], c["annotations"]) for c in scm.check_runs] == [
        ("choregos/scope", landed, "failure", [DENIED[0]])
    ]


# ───────────────────────── every shipped workflow that merges, walked ─────────────────────────


def _by_label(*labels: str) -> list[Any]:
    return [pytest.param(BY_LABEL[label], id=label) for label in labels]


EVERY_WORKFLOW = _by_label(*sorted(BY_LABEL))


async def _refusals(setup: Fixture, transition: Any) -> list[tuple[str, str]]:
    """`(state it went to, reason)` each time the work item left `transition`'s state refused."""
    return [
        (to, reason)
        for origin, to, reason in await journey(setup)
        if origin == transition.from_ and to != transition.to
    ]


def _first_stop(transition: Any) -> str:
    """Where a first refusal of `transition` is seen to send the work item: its `on_fail` state, or
    the escalation when a retry stays where it is or there is none."""
    retry = transition.on_fail
    return retry.to if retry.max_attempts > 1 and retry.to != transition.from_ else retry.escalate_to


async def _the_merge_has_ruled(setup: Fixture, shipped: Shipped) -> None:
    """Wait until the merge transition's gates have judged a commit, whatever they then wait for."""
    for _ in range(600):
        judged = ((await work_item(setup)).documents or {}).get("judged_commit") or {}
        if judged.get("state") == shipped.merge.from_:
            return
        await asyncio.sleep(0.1)
    raise AssertionError(f"the merge transition never ruled: {await journey(setup)}")


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
    for guarantee in ("no_secrets", "scope_respected"):
        last = (await verdicts(setup, guarantee))[-1]
        assert last["passed"] is True and last["head_sha"] == head, f"the commit merged passed {guarantee}"
    assert await _refusals(setup, shipped.merge) == []


@pytest.mark.parametrize("shipped", EVERY_WORKFLOW)
async def test_a_secret_that_lands_before_the_merge_transition_is_not_merged(
    setup: Fixture, temporal_env: Any, shipped: Shipped
) -> None:
    """The gap this story closes. The steps that write were judged on a clean branch; a commit carrying
    a secret lands as the pull request is opened, green and approved. The merge transition reads it,
    refuses, and the work item follows the template's `on_fail` to a person."""
    scm = setup.adapters.scm
    async with walked(setup, temporal_env, shipped, lands=[*shipped.branch, STRAY]) as walk:
        await rests_in(setup, shipped.merge.on_fail.escalate_to, never=shipped.merge.to)

    assert scm.merge_queue == [] and scm.merged_at == {}, "nothing was merged"
    walked_through = await journey(setup)
    assert shipped.merge.to not in [to for _, to, _ in walked_through]
    refusals = await _refusals(setup, shipped.merge)
    assert refusals, walked_through
    assert refusals[0][0] == _first_stop(shipped.merge), refusals
    # A decision record's merge also refuses the file itself: it is no part of the record.
    named = [reason.removeprefix("failing gates: ").split(", ") for _, reason in refusals]
    assert all(
        "no_secrets" in gates and set(gates) <= {"no_secrets", "scope_respected"} for gates in named
    ), named
    [stray] = walk.landed
    refused = [v for v in await verdicts(setup, "no_secrets") if not v["passed"]]
    assert refused and {v["head_sha"] for v in refused} == {stray}, "the verdict names the commit that landed"
    assert refused[0]["annotations"] == ["AWS key"]


@pytest.mark.parametrize("shipped", EVERY_WORKFLOW)
async def test_a_denied_file_that_lands_before_the_merge_transition_is_not_merged(
    setup: Fixture, temporal_env: Any, shipped: Shipped
) -> None:
    """A file under a path the project's policy forbids lands as the pull request is opened, after
    every step that wrote was judged. No step's scope reads it: each run starts from a branch that
    already holds it. The first transition that reads the scope over the whole diff does — the merge,
    or the opening of the pull request where the workflow asks it there —, names the file, and the
    work item follows that transition's `on_fail` to a person."""
    scm = setup.adapters.scm
    guard = shipped.scope_guard
    async with walked(setup, temporal_env, shipped, lands=[*shipped.branch, DENIED]) as walk:
        await rests_in(setup, guard.on_fail.escalate_to, never=shipped.merge.to)

    assert scm.merge_queue == [] and scm.merged_at == {}, "nothing was merged"
    walked_through = await journey(setup)
    assert shipped.merge.to not in [to for _, to, _ in walked_through]
    refusals = await _refusals(setup, guard)
    assert refusals, walked_through
    assert refusals[0][0] == _first_stop(guard), refusals
    assert all(reason == "failing gates: scope_respected" for _, reason in refusals), refusals
    [stray] = walk.landed
    refused = [v for v in await verdicts(setup, "scope_respected") if not v["passed"]]
    assert refused and {v["head_sha"] for v in refused} == {stray}, "the verdict names the commit that landed"
    assert {tuple(v["annotations"]) for v in refused} == {(DENIED[0],)}, "and the file"


@pytest.mark.parametrize(
    "shipped",
    _by_label(
        "core/default-simple", "github-software-delivery/dev-complex", "github-software-delivery/study"
    ),
)
async def test_a_denied_file_that_landed_before_the_last_step_began_is_not_merged(
    setup: Fixture, temporal_env: Any, shipped: Shipped
) -> None:
    """The file lands between two steps: after the first step that writes was judged, before the next
    one begins. Every later step's scope is about its own run, which starts from a branch that
    already holds the file, and passes; the merge reads the work item's whole diff, and refuses."""
    scm = setup.adapters.scm
    first, *later = shipped.scoped_steps
    async with walked(
        setup, temporal_env, shipped, lands=[*shipped.branch, DENIED], after_step=first.id
    ) as walk:
        await rests_in(setup, shipped.merge.on_fail.escalate_to, never=shipped.merge.to)

    assert scm.merge_queue == [] and scm.merged_at == {}, "nothing was merged"
    [stray] = walk.landed
    on_the_commit = [v for v in await verdicts(setup, "scope_respected") if v.get("head_sha") == stray]
    refused = [v for v in on_the_commit if not v["passed"]]
    assert refused and {tuple(v["annotations"]) for v in refused} == {(DENIED[0],)}
    refusals = await _refusals(setup, shipped.merge)
    assert refusals and all(reason == "failing gates: scope_respected" for _, reason in refusals), refusals
    if [step for step in later if step.id not in ("t-address-review", "t-fix-ci")]:
        assert [v for v in on_the_commit if v["passed"]], "a later step's own scope passed on that commit"


@pytest.mark.parametrize("shipped", _by_label("core/architecture-decision", "github-software-delivery/study"))
async def test_a_file_outside_the_paths_a_merge_declares_is_not_merged(
    setup: Fixture, temporal_env: Any, shipped: Shipped
) -> None:
    """A workflow whose steps are all held to the same paths declares them on its merge transition:
    a decision record merges nothing but the record."""
    scm = setup.adapters.scm
    async with walked(setup, temporal_env, shipped, lands=[*shipped.branch, NOT_THE_RECORD]):
        await rests_in(setup, shipped.merge.on_fail.escalate_to, never=shipped.merge.to)

    assert scm.merge_queue == [] and scm.merged_at == {}, "nothing was merged"
    refusals = await _refusals(setup, shipped.merge)
    assert refusals and refusals[0] == (_first_stop(shipped.merge), "failing gates: scope_respected"), (
        refusals
    )
    refused = [v for v in await verdicts(setup, "scope_respected") if not v["passed"]]
    assert refused[0]["annotations"] == [NOT_THE_RECORD[0]]
    assert refused[0]["detail"] == "1 file(s) outside the declared paths"


async def test_what_remains_a_file_outside_the_work_items_allowed_paths_is_not_refused_for_its_path(
    setup: Fixture, temporal_env: Any
) -> None:
    """What the merge does not hold (ADR 0171, decision 13): a work item's own allowed paths. A file
    outside them, and under no forbidden path, that lands before the merge is read by `no_secrets`
    and the other guarantees, and by no scope: the verdict says which paths it held. This test changes
    the day the platform owns those paths."""
    shipped = BY_LABEL["core/default-simple"]
    scm = setup.adapters.scm
    async with walked(setup, temporal_env, shipped, lands=[*shipped.branch, ELSEWHERE]) as walk:
        await rests_in(setup, shipped.merge.to)

    [landed] = walk.landed
    assert list(scm.merged_at.values()) == [landed]
    at_the_merge = (await verdicts(setup, "scope_respected"))[-1]
    assert at_the_merge["passed"] is True and at_the_merge["head_sha"] == landed
    assert "only the paths the policy forbids are held" in at_the_merge["detail"]


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
    refusals = await _refusals(setup, shipped.merge)
    assert refusals and refusals[0] == (shipped.merge.on_fail.to, f"failing gates: {gate}"), refusals


@pytest.mark.parametrize(
    ("shipped", "file", "gate"),
    [
        pytest.param(BY_LABEL["core/default-simple"], STRAY, "no_secrets", id="core/default-simple-a-secret"),
        pytest.param(BY_LABEL["core/advanced"], DENIED, "scope_respected", id="core/advanced-a-denied-file"),
    ],
)
async def test_a_commit_that_lands_while_the_merge_waits_for_its_checks_is_not_merged(
    setup: Fixture, temporal_env: Any, shipped: Shipped, file: tuple[Any, ...], gate: str
) -> None:
    """The merge transition was entered on a clean branch and waits for its checks; the commit lands
    then. Once the checks answer, every guarantee is read again on the head as it stands."""
    scm = setup.adapters.scm
    async with walked(setup, temporal_env, shipped, green=False) as walk:
        await _the_merge_has_ruled(setup, shipped)
        stray = scm.push(REPOSITORY, BRANCH, [*shipped.branch, file])
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
    refused = [v for v in await verdicts(setup, gate) if not v["passed"]]
    assert refused and {v["head_sha"] for v in refused} == {stray}
    refusals = await _refusals(setup, shipped.merge)
    assert refusals and all(reason == f"failing gates: {gate}" for _, reason in refusals), refusals
