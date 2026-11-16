"""What is merged is what was judged (S65-05, ADR 0171).

The gates of a work item used to judge its branch by its name, and the merge named no commit: a
branch whose head moved after the gates had ruled was merged as it then stood. An evaluation now
reads the head of the branch once and judges that commit; the merge names it, and the forge refuses
a pull request whose head is elsewhere.

Played on `FakeScm`, which has commits as a forge does: a write moves a branch's head, a commit
keeps the diff it was pushed with, and a merge on a stale commit is refused.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from .conftest import Fixture, stage_result
from .kits.workers import worker_with
from .test_interpreter import _pr_ref, _ticket, _wait_state, scripted, start

pytestmark = pytest.mark.asyncio

REPOSITORY = "varga/billing-api"
BRANCH = "choregos/123"
#: What the fixture's branch holds: inside the work item's allowed paths.
IN_SCOPE = [("src/orders/total.py", 20, 4), ("tests/orders/test_total.py", 30, 0)]
#: A commit nobody judged: a file outside the allowed paths.
OUT_OF_SCOPE = [*IN_SCOPE, (".github/workflows/release.yml", 12, 0)]
DIFF_GATES = [{"name": "scope_respected"}, {"name": "no_secrets"}, {"name": "diff_size_max"}]


def _ids(setup: Fixture, **more: Any) -> dict[str, Any]:
    return {"project_id": setup.project_id, "work_item_id": setup.work_item_id, **more}


async def _run(setup: Fixture, *, commits: list[str], start_commit: str = "") -> str:
    """A finished implement run, as the runner declares it: its branch, its commits, where it began."""
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    result = stage_result("implemented", artifacts={"branch": BRANCH, "commits": commits})
    if start_commit:
        result.artifacts.reports["start_commit"] = start_commit
    async with session_scope() as session:
        run = Run(
            id=f"{setup.work_item_id}-t-implement-1",
            work_item_id=setup.work_item_id,
            project_id=setup.project_id,
            transition_id="t-implement",
            stage_role="implement",
            attempt=1,
            status="succeeded",
            result=result.model_dump(mode="json", by_alias=True),
        )
        session.add(run)
    return str(run.id)


async def _open_pull_request(setup: Fixture, *, state: str = "pr_open") -> Any:
    """The work item's pull request, open and green, the work item waiting in its merge state."""
    from choregos_api.db.models import WorkItem
    from choregos_api.db.session import session_scope

    scm = setup.adapters.scm
    ref = await scm.open_pr(REPOSITORY, BRANCH, "main", "fix: credits", "body", False)
    scm.set_checks(ref, "success")
    async with session_scope() as session:
        item = await session.get(WorkItem, setup.work_item_id)
        assert item is not None
        item.pr_url = ref.url
        item.state = state
    return ref


async def _judged(setup: Fixture) -> dict[str, Any] | None:
    """What the gates left on the work item for the merge."""
    return ((await _ticket(setup)).documents or {}).get("judged_commit")


async def _gate_journal(run_id: str) -> list[dict[str, Any]]:
    from choregos_api.db.models import RunEvent
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        rows = await session.execute(
            select(RunEvent).where(RunEvent.run_id == run_id, RunEvent.type == "gate.outcome")
        )
        return [dict(r.payload) for r in rows.scalars()]


# ───────────────────────── the gates judge a commit ─────────────────────────


async def test_the_gates_read_the_head_once_and_judge_that_commit(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every comparison of an evaluation carries the commit read once, never the branch's name; the
    verdicts are journaled with it and it is left on the work item for the merge."""
    from choregos_orchestrator.activities.gates import evaluate_gates

    scm = setup.adapters.scm
    scm.set_diff(REPOSITORY, "base0", BRANCH, IN_SCOPE)  # what the run wrote since it began
    run_id = await _run(setup, commits=["c0ffee"], start_commit="base0")
    head = await scm.head_of(REPOSITORY, BRANCH)
    reads: list[str] = []
    compared: list[tuple[str, str]] = []
    read_head, compare = scm.head_of, scm.compare

    async def counted_head(repo: str, branch: str) -> Any:
        reads.append(branch)
        return await read_head(repo, branch)

    async def recorded_compare(repo: str, base: str, to: str) -> Any:
        compared.append((base, to))
        return await compare(repo, base, to)

    monkeypatch.setattr(scm, "head_of", counted_head)
    monkeypatch.setattr(scm, "compare", recorded_compare)

    verdicts = await evaluate_gates(_ids(setup, run_id=run_id, gates=DIFF_GATES))

    assert all(v["passed"] for v in verdicts), verdicts
    assert reads == [BRANCH], "the head of the branch is read once per evaluation"
    assert compared == [("main", head), ("base0", head)], "the diff and the run's scope, both at the commit"
    assert {v["head_sha"] for v in await _gate_journal(run_id)} == {head}, "each verdict names its commit"
    assert await _judged(setup) == {"sha": head, "repository": REPOSITORY, "branch": BRANCH, "state": "inbox"}


async def test_a_commit_that_lands_during_the_evaluation_is_not_the_one_judged(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A push between two reads of the same evaluation: the verdict is about the commit that was read,
    which is the one recorded — the later one is not judged, and is not what a merge would name."""
    from choregos_orchestrator.activities.gates import evaluate_gates

    scm = setup.adapters.scm
    read_head = scm.head_of
    landed: list[str] = []

    async def read_then_a_push_lands(repo: str, branch: str) -> Any:
        head = await read_head(repo, branch)
        landed.append(scm.push(REPOSITORY, BRANCH, OUT_OF_SCOPE))
        return head

    judged = await scm.head_of(REPOSITORY, BRANCH)
    monkeypatch.setattr(scm, "head_of", read_then_a_push_lands)

    [scope] = await evaluate_gates(_ids(setup, gates=[{"name": "scope_respected"}]))

    assert scope["passed"] is True, "the commit that was read is inside the allowed paths"
    record = await _judged(setup)
    assert record is not None and record["sha"] == judged != landed[0]


async def test_judging_twice_records_the_same_thing(setup: Fixture) -> None:
    from choregos_orchestrator.activities.gates import evaluate_gates

    payload = _ids(setup, gates=[{"name": "no_secrets"}])
    first = await evaluate_gates(payload)
    recorded = await _judged(setup)
    assert await evaluate_gates(payload) == first
    assert await _judged(setup) == recorded


async def test_a_branch_whose_head_cannot_be_read_is_not_judged(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a commit there is nothing to judge: the diff guarantees refuse, and nothing is recorded
    that a merge could name."""
    from choregos_adapters.errors import UpstreamError
    from choregos_orchestrator.activities.gates import evaluate_gates

    async def unreadable(repo: str, branch: str) -> Any:
        raise UpstreamError("github", "GET ref → 502", status_code=502)

    monkeypatch.setattr(setup.adapters.scm, "head_of", unreadable)
    verdicts = await evaluate_gates(_ids(setup, gates=DIFF_GATES))
    assert all(not v["passed"] and "diff unavailable" in v["detail"] for v in verdicts), verdicts
    assert await _judged(setup) is None


async def test_the_check_runs_carry_the_judged_commit(setup: Fixture) -> None:
    """`choregos/scope` and `choregos/evidence` mark the commit the verdict is about, not the one the
    run declared."""
    from choregos_orchestrator.activities.gates import evaluate_gates

    scm = setup.adapters.scm
    run_id = await _run(setup, commits=["declared-by-the-run"])
    await _open_pull_request(setup)
    head = await scm.head_of(REPOSITORY, BRANCH)

    await evaluate_gates(
        _ids(setup, run_id=run_id, gates=[{"name": "scope_respected"}, {"name": "evidence_present"}])
    )

    assert [(c["name"], c["sha"]) for c in scm.check_runs] == [
        ("choregos/scope", head),
        ("choregos/evidence", head),
    ]


async def test_the_checks_of_another_head_say_nothing_of_the_judged_commit(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The pull request moved after the head was read: its green checks are another commit's, and
    `ci_green` stays pending instead of passing on them."""
    from choregos_orchestrator.activities.gates import evaluate_gates

    scm = setup.adapters.scm
    await _open_pull_request(setup)
    payload = _ids(setup, gates=[{"name": "ci_green"}])
    [green] = await evaluate_gates(payload)
    assert green["passed"] is True, "the pull request's head is the judged commit: its checks count"

    get_pr = scm.get_pr

    async def a_push_lands_then_the_pull_request_is_read(ref: Any) -> Any:
        scm.push(REPOSITORY, BRANCH)
        return await get_pr(ref)

    monkeypatch.setattr(scm, "get_pr", a_push_lands_then_the_pull_request_is_read)
    [moved] = await evaluate_gates(payload)
    assert moved["passed"] is False and moved["pending"] is True, moved


# ───────────────────────── the merge names the commit that was judged ─────────────────────────


async def test_the_merge_names_the_judged_commit(setup: Fixture) -> None:
    from choregos_orchestrator.activities.gates import evaluate_gates
    from choregos_orchestrator.activities.scm import enqueue_merge

    scm = setup.adapters.scm
    ref = await _open_pull_request(setup)
    [green] = await evaluate_gates(_ids(setup, gates=[{"name": "ci_green"}]))
    assert green["passed"] is True
    head = await scm.head_of(REPOSITORY, BRANCH)

    merged = await enqueue_merge(_ids(setup))

    assert merged == {"enqueued": True, "pr_url": ref.url, "sha": head}
    assert scm.merged_at == {(REPOSITORY, ref.number): head}
    assert [(r.repo, r.number) for r in scm.merge_queue] == [(REPOSITORY, ref.number)]


async def test_a_commit_that_lands_after_the_gates_is_not_merged(setup: Fixture) -> None:
    """The gap this story closes: an extra commit after the verdict. The merge names the judged
    commit, the forge refuses it, and the refusal is typed and final — until the gates judge again."""
    from choregos_orchestrator.activities.gates import evaluate_gates
    from choregos_orchestrator.activities.scm import HEAD_MOVED, enqueue_merge

    scm = setup.adapters.scm
    await _open_pull_request(setup)
    gates = [{"name": "ci_green"}, {"name": "scope_respected"}]
    assert all(v["passed"] for v in await evaluate_gates(_ids(setup, gates=gates)))
    judged = await scm.head_of(REPOSITORY, BRANCH)
    unjudged = scm.push(REPOSITORY, BRANCH, OUT_OF_SCOPE)

    for _ in range(2):  # asked again, it is refused again: never a merge without the commit
        with pytest.raises(ApplicationError) as refused:
            await enqueue_merge(_ids(setup))
        assert refused.value.type == HEAD_MOVED and refused.value.non_retryable
        assert judged[:12] in str(refused.value) and unjudged[:12] in str(refused.value)
        assert "Nothing was merged" in str(refused.value)
    assert scm.merge_queue == [] and scm.merged_at == {}, "nothing unjudged was merged"

    # Judged again, the new head is refused by the guarantee that reads it, and is still not merged.
    again = {v["name"]: v for v in await evaluate_gates(_ids(setup, gates=gates))}
    assert again["scope_respected"]["passed"] is False
    assert again["scope_respected"]["annotations"] == [".github/workflows/release.yml"]


async def test_a_moved_head_that_is_judged_again_is_merged_at_the_new_commit(setup: Fixture) -> None:
    from choregos_orchestrator.activities.gates import evaluate_gates
    from choregos_orchestrator.activities.scm import enqueue_merge

    scm = setup.adapters.scm
    ref = await _open_pull_request(setup)
    gates = [{"name": "ci_green"}, {"name": "scope_respected"}]
    await evaluate_gates(_ids(setup, gates=gates))
    later = scm.push(REPOSITORY, BRANCH, IN_SCOPE)
    with pytest.raises(ApplicationError):
        await enqueue_merge(_ids(setup))

    assert all(v["passed"] for v in await evaluate_gates(_ids(setup, gates=gates)))
    await enqueue_merge(_ids(setup))
    assert scm.merged_at == {(REPOSITORY, ref.number): later}


async def test_merging_twice_is_not_a_second_merge(setup: Fixture) -> None:
    """ADR 0008: the activity replayed after its merge went through asks for nothing more."""
    from choregos_orchestrator.activities.gates import evaluate_gates
    from choregos_orchestrator.activities.scm import enqueue_merge

    scm = setup.adapters.scm
    await _open_pull_request(setup)
    await evaluate_gates(_ids(setup, gates=[{"name": "ci_green"}]))

    assert await enqueue_merge(_ids(setup)) == await enqueue_merge(_ids(setup))
    assert len(scm.merge_queue) == 1


async def test_a_connector_that_cannot_name_a_commit_is_not_asked_to_merge(
    setup: Fixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0101: a merge the platform cannot hold to the judged commit is refused, not made blind."""
    from choregos_orchestrator.activities.gates import evaluate_gates
    from choregos_orchestrator.activities.scm import MERGE_UNPINNED, enqueue_merge

    scm = setup.adapters.scm
    await _open_pull_request(setup)
    await evaluate_gates(_ids(setup, gates=[{"name": "ci_green"}]))
    asked: list[Any] = []

    async def blind_merge(ref: Any, *args: Any) -> None:
        asked.append(ref)

    monkeypatch.setattr(scm, "capabilities", frozenset())
    monkeypatch.setattr(scm, "enqueue_merge", blind_merge)
    with pytest.raises(ApplicationError) as refused:
        await enqueue_merge(_ids(setup))
    assert refused.value.type == MERGE_UNPINNED and refused.value.non_retryable
    assert "merge_at_commit" in str(refused.value)
    assert asked == [], "the connector was never asked"


async def test_a_merge_transition_without_gates_names_the_head_it_reads(setup: Fixture) -> None:
    """No gate ruled in the state the work item merges from: a verdict given in an earlier state is
    not this merge's. The head is read once, by the merge, and named all the same."""
    from choregos_orchestrator.activities.gates import evaluate_gates
    from choregos_orchestrator.activities.scm import HEAD_MOVED, enqueue_merge

    scm = setup.adapters.scm
    await evaluate_gates(_ids(setup, gates=DIFF_GATES))  # judged in `inbox`, an earlier state
    earlier = await scm.head_of(REPOSITORY, BRANCH)
    ref = await _open_pull_request(setup, state="approved")
    later = scm.push(REPOSITORY, BRANCH, IN_SCOPE)  # a later step wrote: its head is the pull request's

    race = [lambda _ref: scm.push(REPOSITORY, BRANCH)]
    scm.before_merge.extend(race)
    with pytest.raises(ApplicationError) as refused:
        await enqueue_merge(_ids(setup))
    assert refused.value.type == HEAD_MOVED, "a push that races the merge is refused here too"
    assert scm.merge_queue == []

    scm.before_merge.clear()
    current = await scm.head_of(REPOSITORY, BRANCH)
    merged = await enqueue_merge(_ids(setup))
    assert merged["sha"] == current and current not in (earlier, later)
    assert scm.merged_at == {(REPOSITORY, ref.number): current}


# ───────────────────────── end to end, with a real interpreter ─────────────────────────


async def test_an_extra_commit_after_the_gates_stops_the_work_item_and_nothing_is_merged(
    setup: Fixture, temporal_env: Any
) -> None:
    """`default-simple` up to its merge: the gates pass, then a commit lands before the merge is asked.
    The work item's workflow stops with the reason, nothing is merged, the work item is not `merged`;
    started again, its gates judge the new head and the merge names that one."""
    from choregos_orchestrator.activities.scm import enqueue_merge
    from temporalio import activity
    from temporalio.client import WorkflowFailureError

    scm = setup.adapters.scm
    landed: list[str] = []
    asked: list[dict[str, Any]] = []

    @activity.defn(name="enqueue_merge")
    async def a_commit_lands_then_the_merge_runs(payload: dict[str, Any]) -> dict[str, Any]:
        """Between the last verdict and the merge, once: the commit nobody judged."""
        asked.append(payload)
        if not landed:
            landed.append(scm.push(REPOSITORY, BRANCH, OUT_OF_SCOPE))
        return await enqueue_merge(payload)

    scripted(
        setup.adapters,
        stage_result("spec written", outputs={"size": "M", "risk": "low", "spec_markdown": "## Spec"}),
        stage_result("implemented", artifacts={"branch": BRANCH, "commits": ["fix(orders): credits"]}),
        stage_result("verified"),
    )
    setup.adapters.cd.set_health("billing-api", "Healthy")

    async with worker_with(temporal_env, {"enqueue_merge": a_commit_lands_then_the_merge_runs}):
        handle = await start(temporal_env, setup)
        await _wait_state(handle, "awaiting_spec_approval")
        await handle.signal(
            "human_decision", {"approved": True, "decided_by": "augustin", "kind": "approval"}
        )
        await _wait_state(handle, "pr_open")
        judged = await scm.head_of(REPOSITORY, BRANCH)
        scm.set_checks(_pr_ref(setup), "success")
        scm.submit_review(_pr_ref(setup), "marie", "approved")
        for number, event in enumerate(("scm.check.completed", "scm.pr.review_submitted")):
            payload = {"conclusion": "success"} if number == 0 else {"state": "approved"}
            await handle.signal(
                "inbound",
                {"type": event, "source": "github", "delivery_id": f"d{number}", "payload": payload},
            )
        with pytest.raises(WorkflowFailureError):
            # Bounded: merged, the work item would go on to wait for its release train, forever.
            await asyncio.wait_for(handle.result(), timeout=120)

        assert len(asked) == 1, "the merge is not retried on a head that moved"
        assert scm.merge_queue == [] and scm.merged_at == {}, "nothing unjudged was merged"
        item = await _ticket(setup)
        assert item.state == "pr_open", "the work item never became `merged`"
        assert item.failure is not None and item.failure["activity"] == "enqueue_merge"
        assert "Nothing was merged" in item.failure["message"]
        assert judged[:12] in item.failure["message"] and landed[0][:12] in item.failure["message"]

        # Started again from where it stopped, the gates judge the head as it stands now.
        scm.push(REPOSITORY, BRANCH, IN_SCOPE)  # the stray file is taken back
        current = await scm.head_of(REPOSITORY, BRANCH)
        restarted = await start(temporal_env, setup)
        await _wait_state(restarted, "merged")
        assert scm.merged_at == {(REPOSITORY, _pr_ref(setup).number): current}
        await restarted.terminate("end of the test")
