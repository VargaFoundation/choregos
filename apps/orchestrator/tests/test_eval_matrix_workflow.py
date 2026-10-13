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
