"""A job step (ADR 0056): the runner runs a command instead of an agent.

The test that matters is the first one: without the job branch, the runner looks for an agent
backend called `job`, finds none, and the step never runs — and the facts are only measured
because the platform ran the code that produced them.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import AgentRef, JobSpec, ModelRef, StageInput, StageStatus
from choregos_core.redaction import REDACTED
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient

pytestmark = pytest.mark.asyncio

#: A job as a backtest would be: reads the campaign from the fields, writes a file and its facts.
JOB = r"""
import json, os, pathlib, sys
print("calling with " + os.environ["CHOREGOS_RUN_TOKEN"], file=sys.stderr)
ws = pathlib.Path(os.environ["CHOREGOS_WORKSPACE"])
fields = json.loads((ws / ".choregos" / "fields.json").read_text())
out = ws / ".choregos" / "outputs"
out.mkdir(parents=True, exist_ok=True)
(out / "results.csv").write_text("symbol\n" + "\n".join(fields["symbols"]) + "\n")
(ws / ".choregos" / "job.json").write_text(json.dumps({
    "summary": "backtest of " + ",".join(fields["symbols"]),
    "facts": {
        "relevant_count": 2,
        "best_oos_dsr": 0.97,
        "token_seen": os.environ.get("CHOREGOS_RUN_TOKEN") == "tok",
        "run_id": os.environ.get("CHOREGOS_RUN_ID"),
    },
    "outputs": {"decision": "relevant"},
}))
sys.exit(int(os.environ.get("JOB_EXIT", "0")))
"""


def _job(stage_input: StageInput, tmp_path: Path, code: str = JOB, **transition: Any) -> StageInput:
    script = tmp_path / "job.py"
    script.write_text(code, encoding="utf-8")
    return stage_input.model_copy(
        update={
            "repo": None,
            "agent": AgentRef(backend="job"),
            "model": ModelRef(litellm_model="none", base_url=""),
            "gateway_key": None,
            "job": JobSpec(command=[sys.executable, str(script)]),
            "transition": stage_input.transition.model_copy(
                update={
                    "role": "backtest",
                    "outputs": ["decision", "results"],
                    "output_types": {"results": "csv"},
                    **transition,
                }
            ),
            "project": stage_input.project.model_copy(
                update={"test_command": "", "lint_command": "", "typecheck_command": ""}
            ),
        }
    )


def _client(stage_input: StageInput) -> FakeInternalClient:
    client = FakeInternalClient(stage_input)
    client.ticket = {**client.ticket, "fields": {"symbols": ["SPY", "QQQ"]}}
    return client


async def test_a_job_runs_its_command_and_its_facts_are_measured(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path
) -> None:
    # The StageInput carries the token redacted; the run's Secret (the settings) has it (#519).
    job = _job(stage_input, tmp_path)
    job = job.model_copy(update={"callbacks": job.callbacks.model_copy(update={"run_token": REDACTED})})
    client = _client(job)
    outcome = await Runner(runner_settings, client).execute(job, client)  # type: ignore[arg-type]

    assert outcome.exit_code is Exit.OK, outcome.detail
    result = client.results[-1]
    assert result.status is StageStatus.DONE, result.summary
    assert result.summary == "backtest of SPY,QQQ", "the job read the work item's fields"
    assert result.outputs.model_dump()["decision"] == "relevant"
    facts = result.evidence.facts or {}
    assert facts["relevant_count"] == 2 and facts["best_oos_dsr"] == 0.97
    assert facts["token_seen"] is True, "the job holds the run's real token, to call connectors"
    assert facts["run_id"] == job.run_id, "and the run it calls them for"
    assert {"facts.relevant_count", "facts.best_oos_dsr"} <= set(result.evidence.measured or [])
    assert "results" in result.artifacts.files and "results" in client.outputs
    assert result.artifacts.reports["job_image"] == "runner"
    assert len(result.artifacts.reports["job_command_sha256"]) == 64
    types = [event["type"] for event in client.events]
    assert "job.started" in types and "job.finished" in types
    finished = next(event for event in client.events if event["type"] == "job.finished")
    assert runner_settings.run_token not in json.dumps(finished), "the run page never shows the token"


async def test_the_exit_code_is_the_status(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A job cannot claim success: its `job.json` says done, its exit code says otherwise."""
    monkeypatch.setenv("JOB_EXIT", "3")
    job = _job(stage_input, tmp_path)
    client = _client(job)
    await Runner(runner_settings, client).execute(job, client)  # type: ignore[arg-type]

    result = client.results[-1]
    assert result.status is StageStatus.FAILED
    assert result.reason == "job_exit"
    assert "exited with 3" in result.summary


async def test_a_declared_output_the_job_did_not_write_fails_the_step(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path
) -> None:
    job = _job(stage_input, tmp_path, outputs=["decision", "rationale", "results"])
    client = _client(job)
    await Runner(runner_settings, client).execute(job, client)  # type: ignore[arg-type]

    result = client.results[-1]
    assert result.status is StageStatus.FAILED and result.reason == "missing_outputs"
    assert "rationale" in result.summary


async def test_a_fact_that_is_not_a_simple_value_is_refused(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path
) -> None:
    code = (
        "import json, os, pathlib\n"
        "ws = pathlib.Path(os.environ['CHOREGOS_WORKSPACE'])\n"
        "(ws / '.choregos' / 'job.json').write_text(json.dumps({'facts': {'trades': [1, 2]}}))\n"
    )
    job = _job(stage_input, tmp_path, code=code, outputs=[], output_types={})
    client = _client(job)
    await Runner(runner_settings, client).execute(job, client)  # type: ignore[arg-type]

    result = client.results[-1]
    assert result.status is StageStatus.FAILED and result.reason == "job_result_invalid"
    assert "trades" in result.summary


async def test_an_output_the_contract_refuses_fails_the_step_instead_of_crashing(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path
) -> None:
    """`verdict` is a typed output of the contract: a job writing another value is a failed step,
    never a runner that dies without posting a result."""
    code = (
        "import json, os, pathlib\n"
        "ws = pathlib.Path(os.environ['CHOREGOS_WORKSPACE'])\n"
        "(ws / '.choregos' / 'job.json').write_text(json.dumps({'outputs': {'verdict': 'relevant'}}))\n"
    )
    job = _job(stage_input, tmp_path, code=code, outputs=["verdict"], output_types={})
    client = _client(job)
    await Runner(runner_settings, client).execute(job, client)  # type: ignore[arg-type]

    result = client.results[-1]
    assert result.status is StageStatus.FAILED and result.reason == "job_result_invalid"


async def test_a_job_past_its_minutes_is_stopped(stage_input: StageInput, tmp_path: Path) -> None:
    from choregos_runner.job import run_job

    job = _job(stage_input, tmp_path, code="import time; time.sleep(5)\n")
    job = job.model_copy(update={"budget": job.budget.model_copy(update={"max_minutes": 1})})
    import choregos_runner.job as module

    async def fast(command: Any, **kwargs: Any) -> Any:
        return await original(command, **{**kwargs, "timeout": 0.5})

    original = module.run_command
    module.run_command = fast  # type: ignore[assignment]
    try:
        ran = await run_job(job, tmp_path, {})
    finally:
        module.run_command = original  # type: ignore[assignment]
    assert ran.timed_out


async def test_without_fields_a_job_does_not_run(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path
) -> None:
    """A job computes on the work item's parameters: without them it stops, it does not guess."""
    job = _job(stage_input, tmp_path)
    client = _client(job)

    async def broken() -> dict[str, Any]:
        raise RuntimeError("internal API: 503")

    client.fetch_ticket = broken  # type: ignore[method-assign]
    outcome = await Runner(runner_settings, client).execute(job, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.INPUT_NOT_FOUND
    assert not client.results
    assert not (runner_settings.workspace / ".choregos" / "job.json").exists()


async def test_fields_are_written_sorted(tmp_path: Path) -> None:
    from choregos_runner.job import FIELDS, write_fields

    write_fields(tmp_path, {"b": 1, "a": "x"})
    assert json.loads((tmp_path / FIELDS).read_text()) == {"a": "x", "b": 1}
