"""The orchestrator's suite runs on PostgreSQL in CI (S47-22).

The orchestrator writes straight through the API's models, and the advisory lock of the budget
reservation (`services/budgets.py`, S27-11) only exists on PostgreSQL. The suite's PostgreSQL mode
(`CHOREGOS_TEST_ORCH_ON_POSTGRES=1`, `apps/orchestrator/tests/conftest.py`) existed, but nothing
set the flag: it ran on SQLite alone. These checks keep the `python-postgres` job running it.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"
API_ON_POSTGRES = "CHOREGOS_TEST_SUITE_ON_POSTGRES"
ORCHESTRATOR_ON_POSTGRES = "CHOREGOS_TEST_ORCH_ON_POSTGRES"


def _workflow() -> dict[str, Any]:
    return dict(yaml.safe_load(CI.read_text(encoding="utf-8")))


def _python_job() -> dict[str, Any]:
    """The job that plays the suites on PostgreSQL: a job of its own, beside `python` (SQLite)."""
    return dict(_workflow()["jobs"]["python-postgres"])


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


def test_the_postgresql_job_runs_beside_the_sqlite_one_under_the_same_conditions() -> None:
    """Split out for the wall time: it must still run whenever `python` runs, and gate the merge."""
    jobs = _workflow()["jobs"]
    assert jobs["python-postgres"]["needs"] == jobs["python"]["needs"]
    assert jobs["python-postgres"]["if"] == jobs["python"]["if"]
    assert "python" not in _as_list(jobs["python-postgres"]["needs"]), "it must not wait for the SQLite job"
    assert "python-postgres" in jobs["ci-ok"]["needs"]
    # Neither job plays the PostgreSQL mode twice: the `python` job's steps leave both flags out.
    for step in jobs["python"]["steps"]:
        assert API_ON_POSTGRES not in _env(step) and ORCHESTRATOR_ON_POSTGRES not in _env(step)


def test_the_suites_spread_over_the_runner_cores() -> None:
    """One process took about three hours; xdist and a database per worker (`conftest.py`) fix that."""
    jobs = _workflow()["jobs"]
    steps = [step for name in ("python", "python-postgres") for step in jobs[name]["steps"]]
    runs = [str(step.get("run", "")) for step in steps]
    pytest_runs = [run for run in runs if "uv run pytest" in run]
    assert len(pytest_runs) == 3
    assert all(" -n " in run for run in pytest_runs), pytest_runs
    assert any("--cov" in run for run in pytest_runs), "the coverage run must stay (fail_under)"


def _as_list(value: Any) -> list[str]:
    return [value] if isinstance(value, str) else list(value)
