"""A closed pull request's waiting checks are cancelled, and nothing of it runs.

`.github/workflows/cancel-closed-pr-runs.yml` uses `pull_request_target`, which runs with a
token that can write: what these checks hold is that it fires only on `closed`, checks nothing
out, runs no action at all (so no code from the pull request or from a third party), can only
touch Actions, and cancels runs by the closed head's commit.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "cancel-closed-pr-runs.yml"


def _workflow() -> dict[Any, Any]:
    return dict(yaml.safe_load(WORKFLOW.read_text(encoding="utf-8")))


def _triggers(workflow: dict[Any, Any]) -> dict[str, Any]:
    # PyYAML reads the bare key `on` as the boolean True.
    return dict(workflow.get("on", workflow.get(True)) or {})


def test_it_fires_only_when_a_pull_request_closes() -> None:
    assert _triggers(_workflow()) == {"pull_request_target": {"types": ["closed"]}}


def test_its_token_can_touch_actions_only() -> None:
    assert _workflow()["permissions"] == {"actions": "write"}


def test_it_checks_nothing_out_and_runs_no_action() -> None:
    for job in _workflow()["jobs"].values():
        for step in job["steps"]:
            assert "uses" not in step, step
            assert "github.event.pull_request.title" not in step.get("run", "")
            assert "github.event.pull_request.head.ref" not in step.get("run", "")


def test_it_cancels_by_the_closed_head_commit() -> None:
    step = _workflow()["jobs"]["cancel"]["steps"][0]
    assert step["env"]["HEAD_SHA"] == "${{ github.event.pull_request.head.sha }}"
    assert "head_sha=$HEAD_SHA" in step["run"]
    assert "/cancel" in step["run"]
