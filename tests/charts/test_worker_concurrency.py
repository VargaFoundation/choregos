"""S80-07a: the chart passes each worker queue its concurrency, from the chart's values."""

from __future__ import annotations

import pathlib
import shutil
import subprocess

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")

VARIABLES = {
    "activities": "CHOREGOS_WORKER_MAX_CONCURRENT_ACTIVITIES",
    "workflowTasks": "CHOREGOS_WORKER_MAX_CONCURRENT_WORKFLOW_TASKS",
    "localActivities": "CHOREGOS_WORKER_MAX_CONCURRENT_LOCAL_ACTIVITIES",
    "cachedWorkflows": "CHOREGOS_WORKER_MAX_CACHED_WORKFLOWS",
}


def _worker_env(*arguments: str) -> dict[str, dict[str, str]]:
    """Queue name -> the worker container's literal variables."""
    command = ["helm", "template", "choregos", str(CHART), *arguments]
    rendered = subprocess.run(command, capture_output=True, text=True, check=True).stdout
    found: dict[str, dict[str, str]] = {}
    for doc in yaml.safe_load_all(rendered):
        if not doc or doc.get("kind") != "Deployment":
            continue
        queue = doc["metadata"].get("labels", {}).get("choregos/queue")
        if queue is None:
            continue
        container = next(c for c in doc["spec"]["template"]["spec"]["containers"] if c["name"] == "worker")
        found[queue] = {e["name"]: e["value"] for e in container.get("env", []) if "value" in e}
    return found


def test_every_queue_gets_the_values_the_worker_always_had() -> None:
    env = _worker_env()
    assert set(env) == {"orchestrator", "executor", "tracker", "memory"}
    for variables in env.values():
        assert {name: variables[name] for name in VARIABLES.values()} == {
            "CHOREGOS_WORKER_MAX_CONCURRENT_ACTIVITIES": "20",
            "CHOREGOS_WORKER_MAX_CONCURRENT_WORKFLOW_TASKS": "100",
            "CHOREGOS_WORKER_MAX_CONCURRENT_LOCAL_ACTIVITIES": "100",
            "CHOREGOS_WORKER_MAX_CACHED_WORKFLOWS": "1000",
        }


def test_the_chart_value_reaches_every_queue() -> None:
    env = _worker_env(
        "--set",
        "choregos-orchestrator.concurrency.activities=50",
        "--set",
        "choregos-orchestrator.concurrency.cachedWorkflows=0",
    )
    for variables in env.values():
        assert variables["CHOREGOS_WORKER_MAX_CONCURRENT_ACTIVITIES"] == "50"
        assert variables["CHOREGOS_WORKER_MAX_CACHED_WORKFLOWS"] == "0"
        assert variables["CHOREGOS_WORKER_MAX_CONCURRENT_WORKFLOW_TASKS"] == "100"


def test_a_queue_overrides_the_chart_value_for_itself_only(tmp_path: pathlib.Path) -> None:
    values = tmp_path / "values.yaml"
    values.write_text(
        yaml.safe_dump(
            {
                "choregos-orchestrator": {
                    "queues": [
                        {"name": "orchestrator", "replicas": 1, "resources": {}},
                        {
                            "name": "executor",
                            "replicas": 1,
                            "resources": {},
                            "concurrency": {"activities": 4},
                        },
                    ]
                }
            }
        )
    )
    env = _worker_env("-f", str(values))
    assert env["executor"]["CHOREGOS_WORKER_MAX_CONCURRENT_ACTIVITIES"] == "4"
    assert env["executor"]["CHOREGOS_WORKER_MAX_CONCURRENT_WORKFLOW_TASKS"] == "100"
    assert env["orchestrator"]["CHOREGOS_WORKER_MAX_CONCURRENT_ACTIVITIES"] == "20"
