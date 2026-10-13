"""The CI job that starts the local install and drives a work item through it (S28-04).

Without it, "docker compose up works" is a sentence in a page. These checks keep the job from
quietly losing what makes it a proof: running the compose, driving a work item, being waited for
by the gate, and running whenever the install's own files change.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict[str, Any]:
    return dict(yaml.safe_load(CI.read_text(encoding="utf-8")))


def _job() -> dict[str, Any]:
    return dict(_workflow()["jobs"]["local-install"])


def _runs() -> str:
    return "\n".join(str(step.get("run", "")) for step in _job()["steps"])


def test_the_job_starts_the_compose_and_drives_a_work_item() -> None:
    runs = _runs()
    assert "docker compose up -d --wait" in runs
    assert "tools/local_install_check.py" in runs
    assert "--console-only" not in runs, "the console answering is not a work item running"
    assert (ROOT / "tools/local_install_check.py").exists()


def test_the_job_cleans_up_and_shows_the_logs_when_it_fails() -> None:
    by_condition = {str(step.get("if")): str(step.get("run", "")) for step in _job()["steps"]}
    assert "docker compose logs" in by_condition["failure()"]
    assert "docker compose down -v" in by_condition["always()"]


def test_the_gate_waits_for_it() -> None:
    assert "local-install" in _workflow()["jobs"]["ci-ok"]["needs"]


def test_it_runs_when_the_install_changes_on_main_and_in_the_merge_queue() -> None:
    condition = _job()["if"]
    assert "needs.changes.outputs.local-install == 'true'" in condition
    assert "refs/heads/main" in condition and "merge_group" in condition
    (filter_step,) = [s for s in _workflow()["jobs"]["changes"]["steps"] if s.get("id") == "filter"]
    paths = yaml.safe_load(filter_step["with"]["filters"])["local-install"]
    for path in ("compose.yaml", ".env.example", "dev/local/**", "docker/**", "tools/local_install_check.py"):
        assert path in paths, path
    assert _workflow()["jobs"]["changes"]["outputs"]["local-install"]
