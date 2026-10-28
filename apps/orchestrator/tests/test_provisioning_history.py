"""`ProjectProvisioning` (S47-23): the template's steps run in order, then the project is finished.
Its history replays (`tests/replay`), and the replay suite refuses a workflow that has none."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from temporalio import activity

HISTORY = Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories" / "provisioning-S47-23.json"
WORKFLOW_ID = "prov-replay"
PAYLOAD = {
    "project_id": "project-1",
    "project_slug": "replay",
    "org": "org-1",
    "inputs": {"owner": "acme"},
}
STEPS = [
    {"name": "github.ensure_labels", "params": {}},
    {"name": "github.ensure_webhooks", "params": {"events": ["issues", "pull_request"]}},
]


async def play() -> tuple[dict[str, Any], dict[str, list[Any]], Any]:
    """The workflow against stub activities: its result, what each activity was asked, its history."""
    from choregos_orchestrator.testing import workers_repartis
    from choregos_orchestrator.workflows.provisioning import ProjectProvisioning
    from temporalio.testing import WorkflowEnvironment

    asked: dict[str, list[Any]] = {"load": [], "step": [], "finish": []}

    @activity.defn(name="load_template_steps")
    async def load_template_steps(payload: dict[str, Any]) -> dict[str, Any]:
        asked["load"].append(payload)
        return {"steps": STEPS, "manifest": {}, "missing": False}

    @activity.defn(name="run_provision_step")
    async def run_provision_step(payload: dict[str, Any]) -> dict[str, Any]:
        asked["step"].append(payload)
        return {"status": "succeeded", "step": payload["step"]}

    @activity.defn(name="finish_provisioning")
    async def finish_provisioning(payload: dict[str, Any]) -> dict[str, Any]:
        asked["finish"].append(payload)
        return {}

    environment = await WorkflowEnvironment.start_time_skipping()
    try:
        async with workers_repartis(
            environment.client,
            activites=[load_template_steps, run_provision_step, finish_provisioning],
            workflows=[ProjectProvisioning],
        ):
            handle = await environment.client.start_workflow(
                "ProjectProvisioning", PAYLOAD, id=WORKFLOW_ID, task_queue="test"
            )
            result = await handle.result()
            history = await handle.fetch_history()
    finally:
        await environment.shutdown()
    return result, asked, history


async def test_every_step_runs_in_order_and_the_project_is_finished() -> None:
    result, asked, _ = await play()
    assert result == {"completed": ["github.ensure_labels", "github.ensure_webhooks"], "failed": None}
    assert [payload["step"] for payload in asked["step"]] == [step["name"] for step in STEPS]
    assert asked["step"][1]["params"] == {"events": ["issues", "pull_request"], "owner": "acme"}, (
        "the step's own params and the project's inputs reach the activity together"
    )
    assert all(
        payload["resume"] is True and payload["project_id"] == "project-1" for payload in asked["step"]
    )
    assert asked["finish"] == [{"project_id": "project-1"}], "finished once, after the last step"


async def test_the_archived_history_replays() -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.client import WorkflowHistory
    from temporalio.worker import Replayer

    _, _, history = await play()
    replayer = Replayer(workflows=ALL_WORKFLOWS)
    await replayer.replay_workflow(history)
    archived = json.loads(HISTORY.read_text(encoding="utf-8"))
    await replayer.replay_workflow(WorkflowHistory.from_json(WORKFLOW_ID, archived))


if __name__ == "__main__":  # `python -m` from the repository root: records the archived history again
    import asyncio

    async def _record() -> None:
        _, _, history = await play()
        HISTORY.write_text(history.to_json(), encoding="utf-8")

    asyncio.run(_record())
