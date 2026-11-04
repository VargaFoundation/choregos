"""The CI gate (`ci-ok`): the only one branch protection looks at.

It refused what it knew about — `failure`, `cancelled` — and let the rest through. On 2026-10-05,
GitHub's hosted runners stopped acquiring jobs ("not acquired by Runner of type hosted even after
multiple attempts"): `python` and `conformance-backends` came back `abandoned`, and a PR's gate was
green although no Python test had run. Here the gate's own script is played, against the results
GitHub can return.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest
import yaml

CI = pathlib.Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict:
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def _gate(results: dict[str, str]) -> int:
    """The `ci-ok` script, played with `needs` as GitHub renders it: the exit code."""
    (step,) = [e for e in _workflow()["jobs"]["ci-ok"]["steps"] if "run" in e]
    needs = {job: {"result": result, "outputs": {}} for job, result in results.items()}
    script = step["run"].replace("${{ toJSON(needs) }}", json.dumps(needs, indent=2))
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, check=False).returncode


def _all(result: str) -> dict[str, str]:
    return dict.fromkeys(_workflow()["jobs"]["ci-ok"]["needs"], result)


def test_the_gate_waits_for_every_job_of_the_ci() -> None:
    """A job the gate does not wait for, it does not judge."""
    jobs = set(_workflow()["jobs"]) - {"ci-ok"}
    assert set(_workflow()["jobs"]["ci-ok"]["needs"]) == jobs


def test_succeeded_or_skipped_the_gate_opens() -> None:
    results = _all("success")
    results["charts"] = "skipped"
    assert _gate(results) == 0


@pytest.mark.parametrize("result", ["failure", "cancelled", "abandoned", "timed_out", "something_else"])
def test_any_other_result_closes_the_gate(result: str) -> None:
    results = _all("success")
    results["python"] = result
    assert _gate(results) == 1, f"a `{result}` job got through the gate"
