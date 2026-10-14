"""`EvalMatrix` with its context dimension (S37-09, ADR 0055): one cell per context, in order."""

from __future__ import annotations

from typing import Any

from temporalio import activity


async def _play_matrix(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    from choregos_orchestrator.testing import workers_repartis
    from choregos_orchestrator.workflows.evals import EvalMatrix
    from temporalio.testing import WorkflowEnvironment

    asked: list[dict[str, Any]] = []

    @activity.defn(name="list_fixtures")
    async def list_fixtures(_: dict[str, Any]) -> list[str]:
        return ["py-bug-arrondi"]

    @activity.defn(name="run_eval_cell")
    async def run_eval_cell(cell: dict[str, Any]) -> dict[str, Any]:
        asked.append(cell)
        return {"context": cell.get("context"), "validated": True}

    @activity.defn(name="publish_matrix")
    async def publish_matrix(_: dict[str, Any]) -> dict[str, Any]:
        return {}

    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        async with workers_repartis(
            environment.client,
            activites=[list_fixtures, run_eval_cell, publish_matrix],
            workflows=[EvalMatrix],
            file_du_workflow="evals",
        ):
            result = await environment.client.execute_workflow(
                "EvalMatrix", payload, id="evals-contexts", task_queue="evals"
            )
    finally:
        await environment.shutdown()
    return asked, result


async def test_the_matrix_asks_one_cell_per_context_with_its_context() -> None:
    contexts = ["none", "rtk", "headroom", "rtk+headroom"]
    asked, result = await _play_matrix(
        {"project_slug": "billing-api", "with_memory": [True], "contexts": contexts}
    )
    assert [cell["context"] for cell in asked] == contexts
    assert result["cells"] == 4


async def test_a_matrix_without_contexts_asks_the_none_cells() -> None:
    asked, _ = await _play_matrix({"project_slug": "billing-api"})
    assert [(cell["with_memory"], cell["context"]) for cell in asked] == [(True, "none"), (False, "none")]


async def _play_live(payload: dict[str, Any], *, failing: str = "") -> tuple[list[str], dict[str, Any]]:
    """The live path with stub activities: the names of the activities, in the order they ran."""
    from choregos_orchestrator.testing import workers_repartis
    from choregos_orchestrator.workflows.evals import EvalMatrix
    from temporalio.exceptions import ApplicationError
    from temporalio.testing import WorkflowEnvironment

    calls: list[str] = []
    judged: list[dict[str, Any]] = []

    def stub(name: str, answer: Any) -> Any:
        @activity.defn(name=name)
        async def run(payload: dict[str, Any]) -> Any:
            calls.append(name)
            if name == failing:
                raise ApplicationError(f"{name}: the runner pod was evicted", non_retryable=True)
            if name == "judge_eval_run":
                judged.append(payload)
            return answer(payload) if callable(answer) else answer

        return run

    plan = {"project_id": "p", "work_item_id": "w"}
    activities = [
        stub("list_fixtures", ["py-bug-arrondi", "node-slug"]),
        stub(
            "seed_eval_ticket",
            lambda p: {"skipped": p["fixture"] == "node-slug", "reason": "npm", "plan": plan},
        ),
        stub("prepare_stage", {"run_id": "r1", "stage_input": {"budget": {"max_minutes": 30}}}),
        stub("start_run", None),
        stub("await_run", {"result": {"status": "done"}}),
        stub("record_run_outcome", {}),
        stub("collect_spend", {}),
        stub("judge_eval_run", lambda p: {"fixture": p["fixture"], "ok": True, "skipped": False}),
        stub("summarize_eval_cell", lambda p: {"context": p["context"], "outcomes": p["outcomes"]}),
        stub("run_eval_cell", {}),
        stub("publish_matrix", {}),
    ]
    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        async with workers_repartis(
            environment.client, activites=activities, workflows=[EvalMatrix], file_du_workflow="evals"
        ):
            result = await environment.client.execute_workflow(
                "EvalMatrix", payload, id="evals-live", task_queue="evals"
            )
    finally:
        await environment.shutdown()
    result["judged"] = judged
    return calls, result


async def test_a_live_cell_runs_each_case_as_a_stage_then_judges_it() -> None:
    calls, result = await _play_live(
        {"project_slug": "evals", "with_memory": [False], "contexts": ["rtk"], "live": True}
    )
    assert calls == [
        "list_fixtures",
        "seed_eval_ticket",
        "prepare_stage",
        "start_run",
        "await_run",
        "record_run_outcome",
        "collect_spend",
        "judge_eval_run",
        "seed_eval_ticket",  # node-slug: skipped at seeding, no run
        "summarize_eval_cell",
        "publish_matrix",
    ]
    assert "run_eval_cell" not in calls, "a live cell plays no scripted change"
    (cell,) = result["results"]
    assert cell["context"] == "rtk"
    assert [o["fixture"] for o in cell["outcomes"]] == ["py-bug-arrondi", "node-slug"]
    assert cell["outcomes"][1]["skipped"]


async def test_a_failed_run_is_still_accounted_and_judged() -> None:
    calls, result = await _play_live(
        {"project_slug": "evals", "with_memory": [False], "fixtures": ["py-bug-arrondi"], "live": True},
        failing="await_run",
    )
    # `list_fixtures` is not asked: the payload names the fixtures.
    assert calls == [
        "seed_eval_ticket",
        "prepare_stage",
        "start_run",
        "await_run",
        "collect_spend",
        "judge_eval_run",
        "summarize_eval_cell",
        "publish_matrix",
    ]
    assert "evicted" in result["judged"][0]["failure"]


async def test_a_case_that_cannot_be_prepared_is_skipped_and_the_matrix_goes_on() -> None:
    calls, result = await _play_live(
        {"project_slug": "evals", "with_memory": [False], "fixtures": ["py-bug-arrondi"], "live": True},
        failing="prepare_stage",
    )
    assert calls == ["seed_eval_ticket", "prepare_stage", "summarize_eval_cell", "publish_matrix"]
    (outcome,) = result["results"][0]["outcomes"]
    assert outcome["skipped"] and "could not be prepared" in outcome["reason"]
