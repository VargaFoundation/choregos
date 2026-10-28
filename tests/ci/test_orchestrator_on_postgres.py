"""The orchestrator's suite runs on PostgreSQL in CI (S47-22).

The orchestrator writes straight through the API's models, and the advisory lock of the budget
reservation (`services/budgets.py`, S27-11) only exists on PostgreSQL. The suite's PostgreSQL mode
(`CHOREGOS_TEST_ORCH_ON_POSTGRES=1`, `apps/orchestrator/tests/conftest.py`) existed, but nothing
set the flag: it ran on SQLite alone. These checks keep the `python` job running it.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
API_ON_POSTGRES = "CHOREGOS_TEST_SUITE_SUR_POSTGRES"
ORCHESTRATOR_ON_POSTGRES = "CHOREGOS_TEST_ORCH_ON_POSTGRES"


def _python_job() -> dict[str, Any]:
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    return dict(workflow["jobs"]["python"])


def _env(step: dict[str, Any]) -> dict[str, Any]:
    return dict(step.get("env") or {})


def test_the_orchestrator_suite_runs_on_postgresql_in_ci() -> None:
    job = _python_job()
    steps: list[dict[str, Any]] = list(job["steps"])

    api = [i for i, step in enumerate(steps) if API_ON_POSTGRES in _env(step)]
    assert len(api) == 1, "the API suite on PostgreSQL is the step the orchestrator one follows"
    assert "apps/api/tests" in steps[api[0]]["run"]

    orchestrator = [
        i
        for i, step in enumerate(steps)
        if "apps/orchestrator/tests" in str(step.get("run", ""))
        and _env(step).get(ORCHESTRATOR_ON_POSTGRES) == "1"
    ]
    assert orchestrator, "no step runs apps/orchestrator/tests with CHOREGOS_TEST_ORCH_ON_POSTGRES=1"
    assert len(orchestrator) == 1, "the orchestrator suite on PostgreSQL runs once"
    step = steps[orchestrator[0]]
    # Both suites drop and recreate the `public` schema: the API's one goes first, because its
    # fixture recreates the `vector` extension that a schema reset removes.
    assert orchestrator[0] > api[0], "the orchestrator step must come after the API one"
    assert "if" not in step, "a condition on the step hides what PostgreSQL shows"
    assert not step.get("continue-on-error"), "a failure of the step must fail the job"
    # The mode reads the database from the job's service, as the API suite does.
    assert str(_env(job)["CHOREGOS_TEST_DATABASE_URL"]).startswith("postgresql+asyncpg://")
