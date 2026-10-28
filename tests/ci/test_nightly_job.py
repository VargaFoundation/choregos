"""The nightly workflow does not hide a failure (S47-24).

Two ways a night stayed green on a broken proof: a step marked `continue-on-error` (the conformance
report), and a `live` job that skipped every service whose secrets had vanished. The alert job runs on
`failure()`, so only a red job reaches it. These checks read the workflow the schedule runs.
"""

from __future__ import annotations

import ast
import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
NIGHTLY = ROOT / ".github" / "workflows" / "nightly.yml"
LIVE_TESTS = ROOT / "tests" / "live"
# Opt-in switches of the local stacks (a collector, an LLM Guard), run by their own jobs in ci.yml.
NOT_SECRETS = {"CHOREGOS_LIVE_OTEL", "CHOREGOS_LIVE_LLM_GUARD"}


def _jobs() -> dict[str, Any]:
    return dict(yaml.safe_load(NIGHTLY.read_text(encoding="utf-8"))["jobs"])


def _hides_a_failure(value: object) -> bool:
    """`continue-on-error` is truthy as a boolean, as a string, or as an expression we cannot evaluate."""
    return str(value).strip().lower() not in {"", "false", "0", "none"}


def test_no_nightly_step_hides_a_failure() -> None:
    hidden = []
    for name, job in _jobs().items():
        if _hides_a_failure(job.get("continue-on-error", False)):
            hidden.append(name)
        for step in job.get("steps", []):
            if _hides_a_failure(step.get("continue-on-error", False)):
                hidden.append(f"{name}: {step.get('name') or step.get('uses') or step.get('run')}")
    assert not hidden, f"a failing step must reach the alert job: {hidden}"


def test_the_live_job_requires_the_services_it_has_secrets_for() -> None:
    env = _jobs()["live"]["env"]
    listed = str(env.get("CHOREGOS_LIVE_REQUIRED", "")).split(",")
    required = [item.strip().lower() for item in listed if item.strip()]
    assert required, "without a required service every missing secret is a skip, and the night stays green"
    for service in required:
        prefix = f"CHOREGOS_LIVE_{service.upper()}_"
        variables = [name for name in env if name.startswith(prefix)]
        assert variables, f"{service} is required but the job passes none of its {prefix}* secrets"
        assert all("secrets." in str(env[name]) for name in variables), variables


def test_the_live_job_does_not_run_on_forks() -> None:
    assert "github.repository == 'VargaFoundation/choregos'" in str(_jobs()["live"].get("if", ""))


def test_the_live_job_passes_every_variable_the_live_tests_require() -> None:
    """A variable no job passes is a test that skips every night: the AZURE, ECPHORIA, ROVO and
    REALTIME ones were never in the env."""
    asked: set[str] = set()
    for path in sorted(LIVE_TESTS.glob("test_*.py")):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "require":
                constants = [a.value for a in node.args if isinstance(a, ast.Constant)]
                asked |= {value for value in constants if isinstance(value, str)}
    asked -= NOT_SECRETS
    assert {"CHOREGOS_LIVE_ROVO_ACCESS_TOKEN", "CHOREGOS_LIVE_REALTIME_MODEL"} <= asked
    missing = sorted(asked - set(_jobs()["live"]["env"]))
    assert not missing, f"asked by tests/live, passed by no step of the live job: {missing}"
