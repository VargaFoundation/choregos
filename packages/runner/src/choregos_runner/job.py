# SPDX-License-Identifier: Apache-2.0
"""A job step (ADR 0056): the runner runs a command instead of an agent.

The command reads what an agent would read — `.choregos/task.md`, the declared inputs in
`.choregos/inputs/`, the work item's fields in `.choregos/fields.json` — and writes what an agent
would write: files in `.choregos/outputs/`, and `.choregos/job.json`, whose `outputs` carry the
declared inline outputs (json, markdown: values, not files — ADR 0049):

    {"summary": "...", "facts": {"relevant_count": 2}, "outputs": {"verdict": "..."}}

The exit code is the status (0: done, anything else: failed); the job cannot claim success. Its
`facts` are recorded as *measured* (ADR 0045): the platform ran the code that produced them — which
is the whole difference with an agent, whose facts are what it says. A fact the project measures
with a `dod.facts` command still wins over the job's.
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from choregos_contracts import Evidence, StageInput, StageOutputs, StageResult, StageStatus
from pydantic import ValidationError

from .workspace import run_command

JOB_RESULT = ".choregos/job.json"
FIELDS = ".choregos/fields.json"
#: The report keys a job's result carries: what ran, so that a result can be traced to its code.
REPORT_IMAGE = "job_image"
REPORT_COMMAND = "job_command_sha256"


@dataclass(slots=True)
class JobRun:
    exit_code: int
    duration_s: float
    stdout: str
    stderr: str

    @property
    def timed_out(self) -> bool:
        return self.exit_code == 124 and self.stderr.startswith("timed out")


def command_digest(command: list[str]) -> str:
    return sha256("\0".join(command).encode()).hexdigest()


def write_fields(workspace: Path, fields: dict[str, Any]) -> None:
    """The work item's fields (ADR 0031), as the job reads them: a campaign's universe and dates."""
    path = workspace / FIELDS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(fields, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


async def run_job(stage_input: StageInput, workspace: Path, env: dict[str, str]) -> JobRun:
    """Runs the job's argv in the workspace, bounded by the step's minutes; never raises on failure."""
    assert stage_input.job is not None
    started = time.monotonic()
    done = await run_command(
        list(stage_input.job.command),
        cwd=workspace,
        env=env,
        timeout=stage_input.budget.max_minutes * 60,
    )
    return JobRun(done.code, round(time.monotonic() - started, 2), done.stdout, done.stderr)


def job_result(stage_input: StageInput, workspace: Path, ran: JobRun) -> tuple[StageResult, Evidence]:
    """The step's result and the evidence the job measured, from its exit code and `job.json`."""
    declared: dict[str, Any] = {}
    problem = ""
    path = workspace / JOB_RESULT
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                raise ValueError("not a JSON object")
            declared = loaded
        except (ValueError, UnicodeDecodeError) as exc:
            problem = f"`{JOB_RESULT}` is unreadable: {exc}"
    facts, bad = _facts(declared.get("facts"))
    if bad:
        problem = problem or f"`{JOB_RESULT}`: facts must be strings, numbers or booleans ({', '.join(bad)})"
    raw_outputs = declared.get("outputs")
    outputs: dict[str, Any] = raw_outputs if isinstance(raw_outputs, dict) else {}
    summary = str(declared.get("summary") or "").strip()

    if ran.exit_code != 0:
        status, reason = StageStatus.FAILED, "timeout" if ran.timed_out else "job_exit"
        tail = (ran.stderr or ran.stdout).strip()[-300:]
        summary = f"job exited with {ran.exit_code}" + (f": {tail}" if tail else "")
    elif problem:
        status, reason, summary = StageStatus.FAILED, "job_result_invalid", problem
    else:
        status, reason = StageStatus.DONE, None
        missing = [name for name in _inline(stage_input) if outputs.get(name) in (None, "", [], {})]
        if missing:
            status, reason = StageStatus.FAILED, "missing_outputs"
            summary = f"the job did not write the declared output(s) {', '.join(missing)} in `{JOB_RESULT}`"
    summary = (summary or f"job `{stage_input.transition.role}` done")[:4000]
    try:
        result = StageOutputs.model_validate(outputs if status is StageStatus.DONE else {})
    except ValidationError as exc:
        status, reason = StageStatus.FAILED, "job_result_invalid"
        summary = f"`{JOB_RESULT}`: outputs refused by the contract: {str(exc)[:500]}"
        result = StageOutputs()
    stage = StageResult(status=status, reason=reason, summary=summary, outputs=result)
    return stage, Evidence(facts=facts or None)


def _inline(stage_input: StageInput) -> list[str]:
    files = set(stage_input.transition.file_outputs())
    return [name for name in stage_input.transition.outputs if name not in files]


def _facts(raw: Any) -> tuple[dict[str, str | int | float | bool], list[str]]:
    if not isinstance(raw, dict):
        return {}, ([] if raw is None else ["facts"])
    kept: dict[str, str | int | float | bool] = {}
    bad: list[str] = []
    for key, value in raw.items():
        if isinstance(value, str | int | float | bool) and not (
            isinstance(value, float) and math.isnan(value)
        ):
            kept[str(key)] = value
        else:
            bad.append(str(key))
    return kept, bad
