"""The simulated agent of the local install (`compose.yaml`, S28-01).

The worker rebuilds a project's adapters for every activity: `start_run` and `await_run` each get
their own executor. A simulated agent that lived in one instance would be forgotten by the next,
and `await_run` would poll an `unknown` run until the stage timed out.
"""

from __future__ import annotations

from choregos_adapters import build
from choregos_contracts import (
    AgentRef,
    Budget,
    Callbacks,
    ModelRef,
    PlaybookRef,
    ProjectRef,
    StageInput,
    StageStatus,
    TransitionRef,
    WorkItemRef,
)
from choregos_core.domain import ExecRef, StageJobSpec


def _spec(run_id: str, outputs: list[str]) -> StageJobSpec:
    stage_input = StageInput(
        run_id=run_id,
        attempt=1,
        project=ProjectRef(slug="demo", org="demo"),
        work_item=WorkItemRef(key="DEMO-1", title="Plan the offsite", body=""),
        transition=TransitionRef(
            id="t-plan", role="plan", **{"from": "inbox"}, to="awaiting_approval", outputs=outputs
        ),
        agent=AgentRef(backend="claude-code"),
        model=ModelRef(
            litellm_model="platform/standard", base_url="http://gateway:4000", api_format="openai"
        ),
        budget=Budget(usd=1, max_turns=5, max_minutes=5),
        playbook=PlaybookRef(ref="plan@1"),
        callbacks=Callbacks(api_url="http://api:8000", run_token="tok"),
    )
    return StageJobSpec(
        run_id=run_id,
        project_slug="demo",
        namespace="local",
        runner_image="unused",
        api_url="http://api:8000",
        run_token="tok",
        stage_input=stage_input,
    )


async def test_a_run_started_by_one_instance_is_finished_for_the_next() -> None:
    started_by = build("runtime", "fake", {"simulate": True})
    ref = await started_by.start(_spec("run-sim-1", ["plan_markdown"]))

    awaited_by = build("runtime", "fake", {"simulate": True})
    status = await awaited_by.status(ExecRef(kind=ref.kind, name=ref.name, run_id="run-sim-1"))
    assert status.state == "succeeded"
    result = await awaited_by.fetch_result(ExecRef(kind=ref.kind, name=ref.name, run_id="run-sim-1"))
    assert result is not None and result.status is StageStatus.DONE


async def test_every_declared_output_is_filled_and_the_summary_says_no_model_ran() -> None:
    executor = build("runtime", "fake", {"simulate": True})
    await executor.start(_spec("run-sim-2", ["plan_markdown", "risks"]))
    result = await executor.fetch_result(ExecRef(kind="fake", name="fake-run-sim-2", run_id="run-sim-2"))
    assert result is not None
    outputs = result.outputs.model_dump(exclude_none=True)
    assert set(outputs) == {"plan_markdown", "risks"}
    assert "without calling a model" in result.summary


async def test_the_plain_fake_runtime_stays_one_instance_per_build() -> None:
    """Tests script their own `FakeExecutor`: sharing it would leak runs from one test to the next."""
    assert build("runtime", "fake") is not build("runtime", "fake")
    assert build("runtime", "fake", {"simulate": True}) is build("runtime", "fake", {"simulate": True})
