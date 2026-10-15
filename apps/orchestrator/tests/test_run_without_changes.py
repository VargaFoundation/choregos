"""A run that changed nothing is judged on an EMPTY diff, not refused as "diff unavailable" (#536).

On the dev tenant, `DiametralGroup/choregos-sandbox-dev#18` (`dev-simple`): the bug was already fixed
on `main`, the implement agent changed nothing and pushed no branch, the SCM answered 404 on the
comparison, and `scope_respected` / `no_secrets` / `diff_size_max` refused three attempts in a row.
"""

from __future__ import annotations

from typing import Any

import pytest

pytestmark = pytest.mark.asyncio

BRANCH = "choregos/123"
DIFF_GATES = [
    {"name": "scope_respected", "params": {"paths": ["src/**"]}},
    {"name": "no_secrets"},
    {"name": "diff_size_max", "params": {"files": 20, "lines": 800}},
]


async def _implement_run(
    setup: Any, *, diff_files: int, diff_lines: int, commits: list[str], measured: bool = True
) -> str:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope
    from choregos_contracts import StageResult, StageStatus

    result = StageResult(status=StageStatus.DONE, summary="Already fixed on main: nothing to change")
    result.artifacts.branch = BRANCH
    result.artifacts.commits = commits
    result.artifacts.reports["start_commit"] = "abc123"
    result.evidence.diff_files = diff_files
    result.evidence.diff_lines = diff_lines
    if measured:
        result.evidence.measured = ["diff_files", "diff_lines"]
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
    return run.id


def _scm_answers(setup: Any, monkeypatch: pytest.MonkeyPatch, status_code: int) -> None:
    from choregos_adapters.errors import UpstreamError

    async def compare(repo: str, base: str, head: str) -> Any:
        raise UpstreamError("github", f"compare {base}...{head}", status_code=status_code)

    monkeypatch.setattr(setup.adapters.scm, "compare", compare)


async def _verdicts(setup: Any, run_id: str) -> list[dict[str, Any]]:
    from choregos_orchestrator.activities.gates import evaluate_gates

    payload = {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "run_id": run_id,
        "gates": DIFF_GATES,
    }
    return await evaluate_gates(payload)


async def test_a_run_that_changed_nothing_passes_the_diff_gates(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    _scm_answers(setup, monkeypatch, 404)
    run_id = await _implement_run(setup, diff_files=0, diff_lines=0, commits=[])

    verdicts = await _verdicts(setup, run_id)

    assert all(v["passed"] for v in verdicts), verdicts
    assert not any("unavailable" in v["detail"] for v in verdicts), verdicts
    size = next(v for v in verdicts if v["name"] == "diff_size_max")
    assert size["detail"] == "0 files / 0 lines"


async def test_an_scm_outage_is_still_an_unavailable_diff(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only "the branch does not exist" means empty: a 502 is a failure to read, never a pass."""
    _scm_answers(setup, monkeypatch, 502)
    run_id = await _implement_run(setup, diff_files=0, diff_lines=0, commits=[])

    verdicts = await _verdicts(setup, run_id)

    assert not any(v["passed"] for v in verdicts), verdicts
    assert all("diff unavailable" in v["detail"] for v in verdicts), verdicts


async def test_a_run_whose_push_failed_is_not_an_empty_diff(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The branch is missing because the push was refused: the run DID change something."""
    _scm_answers(setup, monkeypatch, 404)
    run_id = await _implement_run(setup, diff_files=2, diff_lines=40, commits=["d00d"])

    verdicts = await _verdicts(setup, run_id)

    assert all("diff unavailable" in v["detail"] for v in verdicts), verdicts


async def test_a_run_that_does_not_measure_its_diff_is_not_trusted(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Zero lines the runner did not measure is the agent's word, not git's."""
    _scm_answers(setup, monkeypatch, 404)
    run_id = await _implement_run(setup, diff_files=0, diff_lines=0, commits=[], measured=False)

    verdicts = await _verdicts(setup, run_id)

    assert all("diff unavailable" in v["detail"] for v in verdicts), verdicts
